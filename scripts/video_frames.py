"""Extract review frames from official KB videos. Frames are evidence, not procedures."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
VENDOR = ROOT.parent / '.tools'
if VENDOR.is_dir():
    sys.path.insert(0, str(VENDOR))

import imageio_ffmpeg  # type: ignore
import yt_dlp  # type: ignore


def extract_frames(node_id: int, video_id: str) -> dict:
    if not video_id.isascii() or len(video_id) != 11 or not all(c.isalnum() or c in '-_' for c in video_id):
        raise ValueError('Identificador do vídeo inválido.')
    target = ROOT / 'output' / 'curation_media' / str(node_id)
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / 'manifest.json'
    if manifest_path.exists() and list(target.glob('frame-*.jpg')):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest.setdefault('frame_seconds', [int(path.stem.split('-')[-1])
                                              for path in sorted(target.glob('frame-*.jpg'))])
        return manifest
    options = {
        'format': 'bestvideo[height<=720][ext=mp4]/bestvideo[height<=720]/best[height<=720]',
        'outtmpl': str(target / 'source.%(ext)s'), 'quiet': True, 'no_warnings': True,
        'noprogress': True, 'socket_timeout': 20, 'retries': 2,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
    duration = int(info.get('duration') or 0)
    if duration <= 0:
        raise ValueError('Não foi possível determinar a duração do vídeo.')
    files = [p for p in target.glob('source.*') if p.suffix.lower() in ('.mp4', '.webm', '.mkv')]
    if not files:
        raise FileNotFoundError('Arquivo do vídeo não foi gerado.')
    source = files[0]
    # Reserve the opening and closing portions for intro/outro; keep eight review points.
    seconds = sorted({max(1, min(duration-1, round(duration * (0.16 + i * 0.095)))) for i in range(8)})
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for second in seconds:
        frame = target / f'frame-{second:03d}.jpg'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', str(second),
                        '-i', str(source), '-frames:v', '1', '-q:v', '3', str(frame), '-y'],
                       check=True, timeout=30)
    manifest = {'node_id': node_id, 'video_id': video_id,
                'video_url': f'https://www.youtube.com/watch?v={video_id}',
                'duration_seconds': duration, 'frame_seconds': seconds,
                'status': 'frames_extracted_pending_review'}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    import argparse
    from video_knowledge_inventory import build_inventory

    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Extract frames for every unmapped official video')
    parser.add_argument('--limit', type=int, default=0, help='Bound the current batch; 0 means all')
    parser.add_argument('--node', type=int, help='Only this knowledge node')
    args = parser.parse_args()
    records = [r for r in build_inventory() if args.node is None or r['node_id'] == args.node]
    if not args.all and args.node is None:
        parser.error('Provide --node or --all')
    if args.limit:
        records = records[:args.limit]
    for index, record in enumerate(records, 1):
        try:
            result = extract_frames(record['node_id'], record['video_id'])
            print(f'{index}/{len(records)} #{record["node_id"]}: {len(result["frame_seconds"])} quadros', flush=True)
        except Exception as exc:
            error_path = ROOT / 'output' / 'curation_media' / str(record['node_id']) / 'extraction_error.json'
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(json.dumps({'video_id': record['video_id'],
                'error_type': type(exc).__name__, 'message': str(exc)[:500],
                'attempted_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{index}/{len(records)} #{record["node_id"]}: ERRO {type(exc).__name__}: {exc}', flush=True)
