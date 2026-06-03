import logging
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import subprocess
import os
import threading
import queue
import time
import tempfile
import requests
import shutil
import re
import json

#new logging config
logging.basicConfig(
    level=logging.DEBUG,  
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

#Defaults work for a local run; Docker overrides these via docker-compose environment
STREAMRIP_CONFIG = os.environ.get('STREAMRIP_CONFIG', os.path.expanduser('~/.config/streamrip/config.toml'))
DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', os.path.expanduser('~/Music'))
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', '2'))

#Fail loudly at startup if the download dir is unusable, instead of silently per-download
try:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if not os.access(DOWNLOAD_DIR, os.W_OK):
        raise PermissionError(f"directory is not writable")
except OSError as e:
    logger.error("=" * 60)
    logger.error(f"DOWNLOAD_DIR '{DOWNLOAD_DIR}' is not usable: {e}")
    logger.error("All downloads WILL FAIL. Set the DOWNLOAD_DIR environment variable to a writable directory.")
    logger.error("=" * 60)

download_queue = queue.Queue()
active_downloads = {}
download_history = []
sse_clients = []
album_art_cache = {}
cache_lock = threading.Lock()
         
class DownloadWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.current_process = None
        
    def run(self):
        while True:
            task = download_queue.get()
            if task is None:
                break
                
            task_id = task['id']
            url = task['url']
            quality = task.get('quality', 3)
            metadata = task.get('metadata', {})
            
            active_downloads[task_id] = {
                'status': 'downloading',
                'url': url,
                'metadata': metadata,
                'started': time.time()
            }
            
            broadcast_sse({
                'type': 'download_started',
                'id': task_id,
                'metadata': metadata,
                'status': 'downloading'
            })
            
            output_lines = []
            process = None 
            
            try:
                cmd = ['rip']
                if os.path.exists(STREAMRIP_CONFIG):
                    cmd.extend(['--config-path', STREAMRIP_CONFIG])
                cmd.extend(['-f', DOWNLOAD_DIR])
                cmd.extend(['-q', str(quality)])
                cmd.extend(['url', url])

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                )
                
                self.current_process = process
                
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        output_lines.append(line)
                        if len(output_lines) % 10 == 0:  
                            broadcast_sse({
                                'type': 'download_progress',
                                'id': task_id,
                                'output': "\n".join(output_lines[-5:]),
                                'progress': {'raw_output': True}
                            })
                
                process.wait()

                full_output = "\n".join(output_lines)

                if process.returncode != 0:
                    status = 'failed'
                    logger.error(f"Download failed (exit code {process.returncode}): {' '.join(cmd)}")
                    logger.error("rip output (last 30 lines):\n%s", "\n".join(output_lines[-30:]))
                elif re.search(r'Skipping track \d+', full_output) and '─ Downloading' not in full_output:
                    #rip skipped every track (already in its database) and only fetched cover art
                    status = 'skipped'
                    logger.info(f"Already downloaded (marked in streamrip database): {url}")
                else:
                    status = 'completed'

                broadcast_sse({
                    'type': 'download_completed',
                    'id': task_id,
                    'status': status,
                    'metadata': metadata,
                    'output': full_output
                })
                            
            except Exception as e:
                logger.exception(f"Download worker error for {url}")
                broadcast_sse({
                    'type': 'download_error',
                    'id': task_id,
                    'error': str(e),
                    'output': "\n".join(output_lines) if output_lines else str(e)
                })
            
            finally:
                self.current_process = None
                if task_id in active_downloads:
                    del active_downloads[task_id]
                if process and process.poll() is None:
                    process.terminate()
            
            download_queue.task_done()

def broadcast_sse(data):
    message = f"data: {json.dumps(data)}\n\n"
    dead_clients = []
    
    for client in sse_clients:
        try:
            client.put(message)
        except:
            dead_clients.append(client)
    
    for client in dead_clients:
        sse_clients.remove(client)

@app.route('/api/events')
def sse_events():
    def generate():
        q = queue.Queue()
        sse_clients.append(q)
        
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    continue #previous heartbeat check
        finally:
            sse_clients.remove(q)
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  #disable nginx buffering
        }
    )

workers = []
for _ in range(MAX_CONCURRENT_DOWNLOADS):
    worker = DownloadWorker()
    worker.start()
    workers.append(worker)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/sw.js')
def service_worker():
    #Service worker must be served from the root so its scope covers the whole app
    return app.send_static_file('sw.js')

@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    quality = data.get('quality', 3)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    #Validate URL (basic check)
    #youtube-dl for later
    valid_services = ['spotify.com', 'deezer.com', 'tidal.com', 'qobuz.com', 'soundcloud.com', 'youtube.com']
    if not any(service in url.lower() for service in valid_services):
        return jsonify({'error': 'Unsupported service URL'}), 400
    
    metadata = extract_metadata_from_url(url)
    
    task_id = f"dl_{int(time.time() * 1000)}"
    task = {
        'id': task_id,
        'url': url,
        'quality': quality,
        'metadata': metadata 
    }
    
    download_queue.put(task)
    
    return jsonify({'task_id': task_id, 'status': 'queued'})


@app.route('/api/status')
def get_all_status():
    return jsonify({
        'active': active_downloads,
        'history': download_history[-20:],
        'queue_size': download_queue.qsize()
    })

    
@app.route('/api/config', methods=['GET', 'POST'])
def config():
    if request.method == 'GET':
        if os.path.exists(STREAMRIP_CONFIG):
            with open(STREAMRIP_CONFIG, 'r') as f:
                return jsonify({'config': f.read()})
        return jsonify({'config': ''})
    
    elif request.method == 'POST':
        data = request.json
        config_content = data.get('config', '')
        
        try:
            if os.path.exists(STREAMRIP_CONFIG):
                shutil.copy2(STREAMRIP_CONFIG, f"{STREAMRIP_CONFIG}.bak")
            
            os.makedirs(os.path.dirname(STREAMRIP_CONFIG), exist_ok=True)
            with open(STREAMRIP_CONFIG, 'w') as f:
                f.write(config_content)
            
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            

@app.route('/api/search', methods=['POST'])
def search_music():
    data = request.json
    query = data.get('query')
    search_type = data.get('type', 'album')
    source = data.get('source', 'qobuz')
    
    # new logging
    logger.info("=" * 60)
    logger.info("SEARCH REQUEST RECEIVED")
    logger.info(f"Query: '{query}'")
    logger.info(f"Type: {search_type}")
    logger.info(f"Source: {source}")
    logger.info("=" * 60)
    
    if not query:
        logger.warning("No query provided")
        return jsonify({'error': 'Query required'}), 400
    
    if source == 'soundcloud' and search_type in ['album', 'artist']:
        logger.debug(f"SoundCloud doesn't support {search_type} search")
        return jsonify({
            'results': [],
            'query': query,
            'source': source,
            'total_count': 0,
            'message': f'SoundCloud does not support {search_type} searches. Try searching for tracks or playlists instead.'
        })
    
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as tmp_file:
            tmp_path = tmp_file.name
        
        logger.info(f"Created temp file: {tmp_path}")
        
        cmd = ['rip']
        if os.path.exists(STREAMRIP_CONFIG):
            cmd.extend(['--config-path', STREAMRIP_CONFIG])
            logger.info(f"Using config file: {STREAMRIP_CONFIG}")
        else:
            logger.warning(f"Config file not found at: {STREAMRIP_CONFIG}")
        
        cmd.extend(['search', '--output-file', tmp_path])
        cmd.extend([source, search_type, query])
        
        logger.info(f"Executing command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        
        logger.info(f"Command completed with return code: {result.returncode}")
        
        if result.stdout:
            logger.info(f"STDOUT ({len(result.stdout)} chars total):\n{result.stdout}")
        else:
            logger.info("STDOUT: (empty)")
            
        if result.stderr:
            logger.warning(f"STDERR ({len(result.stderr)} chars total):\n{result.stderr}")
        else:
            logger.info("STDERR: (empty)")
        
        if result.returncode != 0:
            logger.error(f"Streamrip command failed with return code {result.returncode}")
            error_msg = "Streamrip search failed"
            
            if result.stdout:
                if 'InvalidAppSecretError' in result.stdout:
                    error_msg = "Invalid Qobuz app secrets. Update your config with valid secrets or run 'rip config --update' in the container."
                elif 'Traceback' in result.stdout:
                    error_msg = "Streamrip encountered an error (check logs for full traceback)"
                elif 'authentication' in result.stdout.lower():
                    error_msg = "Authentication failed - check your Qobuz credentials in config"
                elif 'credentials' in result.stdout.lower():
                    error_msg = "Invalid credentials - check your Qobuz configuration"
                
            return jsonify({
                'error': error_msg,
                'debug_info': {
                    'return_code': result.returncode,
                    'stdout_preview': result.stdout if result.stdout else '',  # Send full output
                    'stderr_preview': result.stderr if result.stderr else '',
                    'command': ' '.join(cmd)
                }
            }), 500
        
        # Check if temp file exists and has content
        if os.path.exists(tmp_path):
            file_size = os.path.getsize(tmp_path)
            logger.info(f"Temp file exists, size: {file_size} bytes")
        else:
            logger.error(f"Temp file does not exist: {tmp_path}")
            
        results = []
        
        try:
            with open(tmp_path, 'r') as f:
                content = f.read()
                logger.info(f"Streamrip search output: {content[:500]}")
                logger.info(f"File content length: {len(content)} characters")
                logger.debug(f"File content (first 500 chars):\n{content[:500]}")
                
                if not content or content.strip() == '':
                    logger.warning("Temp file is empty!")
                    return jsonify({
                        'results': [],
                        'query': query,
                        'source': source,
                        'total_count': 0,
                        'message': 'No results found. The search returned empty results.',
                        'debug_info': {
                            'return_code': result.returncode,
                            'stdout': result.stdout[:200] if result.stdout else '',
                            'stderr': result.stderr[:200] if result.stderr else ''
                        }
                    })
                
                try:
                    search_data = json.loads(content)
                    logger.info(f"Successfully parsed JSON with {len(search_data)} items")
                    
                    for idx, item in enumerate(search_data):
                        item_id = item.get('id', '')
                        media_type = item.get('media_type', search_type)  
                        url = construct_url(item.get('source', source), media_type, item_id)
                        
                        desc = item.get('desc', '')
                        artist = ''
                        title = desc
                        
                        if ' by ' in desc:
                            parts = desc.rsplit(' by ', 1)
                            title = parts[0]
                            artist = parts[1]
                        
                        result_item = {
                            'id': item_id,
                            'service': item.get('source', source),
                            'type': media_type, 
                            'artist': artist if artist else desc,
                            'title': title if artist else '',
                            'desc': desc,
                            'url': url,
                            'album_art': ''
                        }
                        results.append(result_item)
                        
                        if idx < 3:  # Log first 3 results
                            logger.debug(f"Result {idx + 1}: {result_item}")
                            
                except json.JSONDecodeError as e:
                    logger.error("=" * 60)
                    logger.error("JSON PARSE ERROR")
                    logger.error(f"Error: {e}")
                    logger.error(f"Error position: line {e.lineno}, column {e.colno}")
                    logger.error(f"Content length: {len(content)} characters")
                    logger.error(f"Content type: {type(content)}")
                    logger.error(f"Content repr: {repr(content[:200])}")
                    logger.error("-" * 60)
                    logger.error(f"FULL CONTENT (all {len(content)} chars):")
                    logger.error(content)
                    logger.error("=" * 60)
                    
                    # Also log what streamrip actually output
                    logger.error("STREAMRIP STDOUT:")
                    logger.error(result.stdout if result.stdout else "(empty)")
                    logger.error("-" * 60)
                    logger.error("STREAMRIP STDERR:")
                    logger.error(result.stderr if result.stderr else "(empty)")
                    logger.error("=" * 60)
                    
                    return jsonify({
                        'error': 'Failed to parse search results',
                        'debug_info': {
                            'parse_error': str(e),
                            'content_length': len(content),
                            'content_preview': content[:500],
                            'full_content': content,  # Include full content in response
                            'stdout': result.stdout,
                            'stderr': result.stderr
                        }
                    }), 500
                    
        except FileNotFoundError:
            logger.error(f"Temp file not found: {tmp_path}")
            return jsonify({
                'error': 'Search output file not found',
                'debug_info': {
                    'temp_path': tmp_path,
                    'return_code': result.returncode
                }
            }), 500
            
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.debug(f"Removed temp file: {tmp_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove temp file: {e}")
        
        logger.info(f"Returning {len(results)} results")
        
        return jsonify({
            'results': results,
            'query': query,
            'source': source,
            'total_count': len(results)
        })
        
    except subprocess.TimeoutExpired:
        logger.error("Search command timed out after 30 seconds")
        return jsonify({'error': 'Search timed out'}), 500
    except Exception as e:
        logger.exception(f"Unexpected error during search: {e}")
        return jsonify({
            'error': str(e),
            'debug_info': {
                'exception_type': type(e).__name__
            }
        }), 500

@app.route('/api/album-art', methods=['GET'])
def get_album_art():
    source = request.args.get('source')
    media_type = request.args.get('type')
    item_id = request.args.get('id')
    
    if not all([source, media_type, item_id]):
        return jsonify({'error': 'Missing parameters'}), 400
    
    #Todo: handle SoundCloud special case and get correct albums if possible
    if source == 'soundcloud':
        if '|' in item_id:
            item_id = item_id.split('|')[0]
        elif 'soundcloud:tracks:' in item_id:
            match = re.search(r'soundcloud:tracks:(\d+)', item_id)
            if match:
                item_id = match.group(1)

    cache_key = f"{source}_{media_type}_{item_id}"
    if cache_key in album_art_cache:
        cached = album_art_cache[cache_key]
        if isinstance(cached, dict):
            return jsonify(cached)
        return jsonify({'album_art': cached})

    try:
        if source == 'qobuz':
            result = fetch_single_album_art(item_id, media_type, None) 
            album_art_cache[cache_key] = result
            return jsonify({
                'album_art': result.get('album_art', ''),
                'tracks_count': result.get('tracks_count'),
                'release_type': result.get('release_type'),
                'year': result.get('year'),
            })
        
        elif source == 'tidal':
            if media_type == 'artist':
                album_art = f"https://resources.tidal.com/images/{item_id}/750x750.jpg"
            else:
                album_art = f"https://resources.tidal.com/images/{item_id}/320x320.jpg"
                
            if album_art:
                album_art_cache[cache_key] = album_art
                return jsonify({'album_art': album_art})
            return jsonify({'album_art': ''})
        
        elif source == 'deezer':
            if media_type == 'artist':
                try:
                    response = requests.get(f"https://api.deezer.com/artist/{item_id}", timeout=3)
                    if response.status_code == 200:
                        data = response.json()
                        album_art = data.get('picture_medium', data.get('picture', ''))
                        if album_art:
                            album_art_cache[cache_key] = album_art
                            return jsonify({'album_art': album_art})
                except:
                    pass
                return jsonify({'album_art': ''})
            else:
                album_art = f"https://api.deezer.com/{media_type}/{item_id}/image"
                if album_art:
                    album_art_cache[cache_key] = album_art
                    return jsonify({'album_art': album_art})
                return jsonify({'album_art': ''})
        
        elif source == 'soundcloud':
            #SoundCloud doesn't provide easy access to artwork
            #Just return empty and let the frontend handle placeholders
            return jsonify({'album_art': ''})
        
        #Default return for unknown sources
        return jsonify({'album_art': ''})
        
    except Exception as e:
        logger.error(f"Error fetching album art for {source}/{media_type}/{item_id}: {e}")
        return jsonify({'album_art': ''})

@app.route('/api/browse')
def browse_downloads():
    try:
        files = []
        for root, dirs, filenames in os.walk(DOWNLOAD_DIR):
            for filename in filenames:
                if filename.endswith(('.mp3', '.flac', '.m4a', '.opus')):
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, DOWNLOAD_DIR)
                    files.append({
                        'name': rel_path,
                        'size': os.path.getsize(filepath),
                        'modified': os.path.getmtime(filepath)
                    })
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify(files[:100])  
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_qobuz_credentials():
    try:
        if os.path.exists(STREAMRIP_CONFIG):
            with open(STREAMRIP_CONFIG, 'r') as f:
                config_content = f.read()

            app_id = re.search(r'app_id\s*=\s*["\']?([^"\'\n]+)["\']?', config_content)
            token = re.search(r'password_or_token\s*=\s*"([^"]+)"', config_content)

            return {
                'app_id': app_id.group(1).strip() if app_id else '950096963',
                'token': token.group(1).strip() if token else None
            }
    except Exception as e:
        logger.error(f"Error reading Qobuz credentials: {e}")
    return {'app_id': '950096963', 'token': None}


def fetch_single_album_art(item_id, media_type, app_id):
    creds = get_qobuz_credentials()
    if not creds['token']:
        return {}

    try:
        response = requests.get(
            f"https://www.qobuz.com/api.json/0.2/{media_type}/get",
            params={
                'app_id': creds['app_id'],
                f'{media_type}_id': item_id,
            },
            headers={
                'X-App-Id': creds['app_id'],
                'X-User-Auth-Token': creds['token'],
            },
            timeout=3
        )
        if response.status_code == 200:
            data = response.json()
            image = data.get('image', {})

            year = None
            release_date = data.get('release_date_original', '')
            if release_date:
                year = release_date[:4]

            return {
                'album_art': image.get('large') or image.get('small') or image.get('thumbnail') or '',
                'tracks_count': data.get('tracks_count'),
                'release_type': data.get('release_type'),
                'year': year,
            }
    except Exception as e:
        logger.error(f"Error fetching Qobuz album art: {e}")
    return {}

def get_qobuz_app_id():
    try:
        if os.path.exists(STREAMRIP_CONFIG):
            with open(STREAMRIP_CONFIG, 'r') as f:
                config_content = f.read()
                #logger.debug(f"Config file content: {config_content[:200]}...")  # First 200 chars
                
            app_id_match = re.search(r'app_id\s*=\s*["\']?([^"\'\n]+)["\']?', config_content)
            
            if app_id_match:
                app_id = app_id_match.group(1).strip()
                logger.debug(f"Found app_id in config: {app_id}")
                return app_id
            else:
                logger.debug("No app_id found in config, using fallback")
        
        #Return a known working app_id as fallback
        fallback_app_id = "950096963"
        logger.debug(f"Using fallback app_id: {fallback_app_id}")
        return fallback_app_id
        
    except Exception as e:
        logger.error(f"Error extracting app_id: {e}")
        return "950096963"


def construct_url(source, media_type, item_id):
    if not item_id:
        return ''
    
    url_patterns = {
        'qobuz': {
            'album': f'https://open.qobuz.com/album/{item_id}',
            'track': f'https://open.qobuz.com/track/{item_id}',
            'artist': f'https://open.qobuz.com/artist/{item_id}',
            'playlist': f'https://open.qobuz.com/playlist/{item_id}'
        },
        'tidal': {
            'album': f'https://tidal.com/browse/album/{item_id}',
            'track': f'https://tidal.com/browse/track/{item_id}',
            'artist': f'https://tidal.com/browse/artist/{item_id}',
            'playlist': f'https://tidal.com/browse/playlist/{item_id}'
        },
        'deezer': {
            'album': f'https://www.deezer.com/album/{item_id}',
            'track': f'https://www.deezer.com/track/{item_id}',
            'artist': f'https://www.deezer.com/artist/{item_id}',
            'playlist': f'https://www.deezer.com/playlist/{item_id}'
        },
        'soundcloud': {
            'track': f'https://soundcloud.com/{item_id}',
            'album': f'https://soundcloud.com/{item_id}',
            'playlist': f'https://soundcloud.com/{item_id}'
        }
    }
    
    if source in url_patterns and media_type in url_patterns[source]:
        return url_patterns[source][media_type]
    
    return f'https://open.{source}.com/{media_type}/{item_id}'

    

def extract_metadata_from_url(url):
    metadata = {
        'service': None,
        'type': None,
        'id': None,
        'title': None,
        'artist': None,
        'album_art': None
    }
    
    try:
        if 'spotify.com' in url:
            metadata['service'] = 'spotify'
            match = re.search(r'/(album|track|playlist|artist)/([a-zA-Z0-9]+)', url)
            if match:
                metadata['type'] = match.group(1)
                metadata['id'] = match.group(2)
                #Note: Spotify requires OAuth for metadata, so we can't easily fetch it
                
        elif 'qobuz.com' in url:
            metadata['service'] = 'qobuz'
            match = re.search(r'/(album|track|playlist|artist)/([0-9]+)', url)
            if match:
                metadata['type'] = match.group(1)
                metadata['id'] = match.group(2)
                metadata.update(fetch_qobuz_metadata(metadata['id'], metadata['type']))
                
        elif 'tidal.com' in url:
            metadata['service'] = 'tidal'
            match = re.search(r'/(album|track|playlist|artist)/([0-9]+)', url)
            if match:
                metadata['type'] = match.group(1)
                metadata['id'] = match.group(2)
                metadata['album_art'] = f"https://resources.tidal.com/images/{metadata['id']}/320x320.jpg"
                
        elif 'deezer.com' in url:
            metadata['service'] = 'deezer'
            match = re.search(r'/(album|track|playlist|artist)/([0-9]+)', url)
            if match:
                metadata['type'] = match.group(1)
                metadata['id'] = match.group(2)
                metadata.update(fetch_deezer_metadata(metadata['id'], metadata['type']))
                
    except Exception as e:
        logger.error(f"Error extracting metadata from URL: {e}")
    
    return metadata

def fetch_qobuz_metadata(item_id, item_type):
    metadata = {}
    try:
        app_id = get_qobuz_app_id()
        api_base = "https://www.qobuz.com/api.json/0.2"
        
        if item_type == 'album':
            response = requests.get(
                f"{api_base}/album/get",
                params={'album_id': item_id, 'app_id': app_id},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                metadata['title'] = data.get('title', '')
                metadata['artist'] = data.get('artist', {}).get('name', '')
                if 'image' in data:
                    for size in ['small', 'medium', 'large', 'thumbnail']:
                        if size in data['image']:
                            metadata['album_art'] = data['image'][size]
                            break
                            
        elif item_type == 'track':
            response = requests.get(
                f"{api_base}/track/get",
                params={'track_id': item_id, 'app_id': app_id},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                metadata['title'] = data.get('title', '')
                metadata['artist'] = data.get('performer', {}).get('name', '')
                album = data.get('album', {})
                if 'image' in album:
                    for size in ['small', 'medium', 'large', 'thumbnail']:
                        if size in album['image']:
                            metadata['album_art'] = album['image'][size]
                            break
                            
    except Exception as e:
        logger.error(f"Error fetching Qobuz metadata: {e}")
    
    return metadata

def fetch_deezer_metadata(item_id, item_type):
    metadata = {}
    try:
        api_base = "https://api.deezer.com"
        
        if item_type == 'album':
            response = requests.get(f"{api_base}/album/{item_id}", timeout=5)
            if response.status_code == 200:
                data = response.json()
                metadata['title'] = data.get('title', '')
                metadata['artist'] = data.get('artist', {}).get('name', '')
                metadata['album_art'] = data.get('cover_medium', '')
                
        elif item_type == 'track':
            response = requests.get(f"{api_base}/track/{item_id}", timeout=5)
            if response.status_code == 200:
                data = response.json()
                metadata['title'] = data.get('title', '')
                metadata['artist'] = data.get('artist', {}).get('name', '')
                album = data.get('album', {})
                metadata['album_art'] = album.get('cover_medium', '')
                
    except Exception as e:
        logger.error(f"Error fetching Deezer metadata: {e}")
    
    return metadata
    
    
@app.route('/api/download-from-url', methods=['POST'])
def download_from_url():
    data = request.json
    url = data.get('url')
    quality = data.get('quality', 3)
    
    title = data.get('title')
    artist = data.get('artist')
    album_art = data.get('album_art')
    service = data.get('service')
    
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    if title and artist and service:
        metadata = {
            'title': title,
            'artist': artist,
            'album_art': album_art,
            'service': service
        }
    else:
        metadata = extract_metadata_from_url(url)
    
    task_id = f"dl_{int(time.time() * 1000)}"
    task = {
        'id': task_id,
        'url': url,
        'quality': quality,
        'metadata': metadata
    }
    
    download_queue.put(task)
    
    return jsonify({
        'task_id': task_id, 
        'status': 'queued',
        'metadata': metadata
    })
    
    

        

if __name__ == '__main__':
    logger.info("Starting Streamrip Web application...")
    logger.info(f"Config path: {STREAMRIP_CONFIG}")
    logger.info(f"Download directory: {DOWNLOAD_DIR}")
    logger.info(f"Max concurrent downloads: {MAX_CONCURRENT_DOWNLOADS}")
    app.run(host='0.0.0.0', port=5000, debug=False)
