#!/usr/bin/env python3
"""
零配置图片上传工具

默认使用 Catbox（无需 API key）
支持多种图床服务
"""

import sys
import os
import json
from urllib.request import Request, urlopen, HTTPError
from urllib.parse import urlencode


def upload_catbox(file_path):
    """
    Catbox.moe - 完全免费，无需 API key
    单文件最大 200MB，永久保存
    """
    import mimetypes

    # Catbox 使用标准的 multipart/form-data
    boundary = '----WebKitFormBoundary' + os.urandom(16).hex()

    with open(file_path, 'rb') as f:
        file_data = f.read()

    # 获取文件名和 MIME 类型
    filename = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'application/octet-stream'

    # 构造 multipart/form-data
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="reqtype"\r\n\r\n'
        f'fileupload\r\n'
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'
        f'Content-Type: {mime_type}\r\n\r\n'
    ).encode('utf-8') + file_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')

    req = Request('https://catbox.moe/user/api.php', data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

    try:
        with urlopen(req, timeout=60) as response:
            url = response.read().decode('utf-8').strip()
            if url.startswith('http'):
                return url
            else:
                raise Exception(f"Catbox 返回异常: {url}")
    except HTTPError as e:
        error_msg = e.read().decode('utf-8', 'replace') if e.fp else ''
        raise Exception(f"Catbox 上传失败: HTTP {e.code} - {error_msg}")


def upload_telegraph(file_path):
    """
    Telegraph - Telegram 官方服务，稳定可靠
    单文件最大 5MB，仅支持图片
    """
    import mimetypes

    boundary = '----WebKitFormBoundary' + os.urandom(16).hex()

    # 检测文件类型
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type or not mime_type.startswith('image/'):
        mime_type = 'image/png'  # 默认

    with open(file_path, 'rb') as f:
        file_data = f.read()

    # 检查文件大小
    if len(file_data) > 5 * 1024 * 1024:
        raise Exception("Telegraph 仅支持 5MB 以内的图片")

    body_parts = []
    body_parts.append(f'--{boundary}\r\n'.encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"\r\n'.encode()
    )
    body_parts.append(f'Content-Type: {mime_type}\r\n\r\n'.encode())
    body_parts.append(file_data)
    body_parts.append(b'\r\n')
    body_parts.append(f'--{boundary}--\r\n'.encode())

    body = b''.join(body_parts)

    req = Request('https://telegra.ph/upload', data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
    req.add_header('Origin', 'https://telegra.ph')
    req.add_header('Referer', 'https://telegra.ph/')

    try:
        with urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            if isinstance(result, list) and len(result) > 0 and 'src' in result[0]:
                return f"https://telegra.ph{result[0]['src']}"
            else:
                raise Exception(f"Telegraph 返回格式异常: {result}")
    except HTTPError as e:
        error_msg = e.read().decode('utf-8', 'replace') if e.fp else ''
        raise Exception(f"Telegraph 上传失败: HTTP {e.code} - {error_msg}")


def upload_smms(file_path, api_key=None):
    """
    SM.MS - 国内访问快
    匿名: 单文件 5MB，每天 10 张
    注册后: 每分钟 20 次请求
    """
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'

    with open(file_path, 'rb') as f:
        file_data = f.read()

    # 检查文件大小
    if len(file_data) > 5 * 1024 * 1024:
        raise Exception("SM.MS 仅支持 5MB 以内的文件")

    body_parts = []
    body_parts.append(f'--{boundary}\r\n'.encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="smfile"; filename="{os.path.basename(file_path)}"\r\n'.encode()
    )
    body_parts.append(b'Content-Type: application/octet-stream\r\n\r\n')
    body_parts.append(file_data)
    body_parts.append(b'\r\n')
    body_parts.append(f'--{boundary}--\r\n'.encode())

    body = b''.join(body_parts)

    req = Request('https://sm.ms/api/v2/upload', data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('User-Agent', 'genimg-uploader/1.0')

    if api_key:
        req.add_header('Authorization', api_key)

    try:
        with urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            if result.get('success'):
                return result['data']['url']
            elif result.get('code') == 'image_repeated':
                return result['images']
            else:
                raise Exception(f"SM.MS 上传失败: {result.get('message', '未知错误')}")
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ''
        raise Exception(f"SM.MS 上传失败: HTTP {e.code} - {error_body}")


def upload_imgbb(file_path, api_key):
    """
    ImgBB - 需要 API key
    无限上传，但有速率限制
    """
    import base64

    with open(file_path, 'rb') as f:
        file_data = base64.b64encode(f.read()).decode()

    data = urlencode({
        'key': api_key,
        'image': file_data
    }).encode()

    req = Request('https://api.imgbb.com/1/upload', data=data)
    req.add_header('User-Agent', 'genimg-uploader/1.0')

    try:
        with urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            if result.get('success'):
                return result['data']['url']
            else:
                raise Exception(f"ImgBB 上传失败: {result}")
    except HTTPError as e:
        raise Exception(f"ImgBB 上传失败: HTTP {e.code}")


def upload_file(file_path, service='catbox', api_key=None):
    """
    上传文件到指定图床服务

    Args:
        file_path: 本地文件路径
        service: 图床服务名称 (catbox, telegraph, smms, imgbb)
        api_key: API key（某些服务需要）

    Returns:
        公网访问 URL
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        raise Exception("文件为空")

    service = service.lower()

    if service == 'catbox':
        return upload_catbox(file_path)
    elif service == 'telegraph':
        return upload_telegraph(file_path)
    elif service == 'smms':
        return upload_smms(file_path, api_key)
    elif service == 'imgbb':
        if not api_key:
            raise Exception("ImgBB 需要 API key，请使用 --api-key 参数或设置 IMGBB_API_KEY 环境变量")
        return upload_imgbb(file_path, api_key)
    else:
        raise Exception(f"不支持的图床服务: {service}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='零配置图片上传工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
支持的图床服务:
  catbox     Catbox.moe - 默认，无需配置，最大 200MB
  telegraph  Telegraph - Telegram 官方，最大 5MB，仅图片
  smms       SM.MS - 国内快，最大 5MB，可匿名
  imgbb      ImgBB - 需要 API key，无限上传

示例:
  # 使用默认 Catbox
  python3 upload.py image.png

  # 使用 Telegraph
  python3 upload.py image.png --service telegraph

  # 使用 SM.MS（带 API key）
  python3 upload.py image.png --service smms --api-key YOUR_KEY

  # 使用 ImgBB
  python3 upload.py image.png --service imgbb --api-key YOUR_KEY
"""
    )

    parser.add_argument('file', help='要上传的文件路径')
    parser.add_argument(
        '--service', '-s',
        choices=['catbox', 'telegraph', 'smms', 'imgbb'],
        default='catbox',
        help='图床服务 (默认: catbox)'
    )
    parser.add_argument('--api-key', help='API key (某些服务需要)')
    parser.add_argument('--debug', action='store_true', help='显示详细错误信息')

    args = parser.parse_args()

    # 从环境变量读取 API key
    if not args.api_key:
        if args.service == 'imgbb':
            args.api_key = os.environ.get('IMGBB_API_KEY')
        elif args.service == 'smms':
            args.api_key = os.environ.get('SMMS_API_KEY')

    try:
        print(f"正在上传到 {args.service}...", file=sys.stderr)
        url = upload_file(args.file, args.service, args.api_key)
        print(url)
        return 0
    except Exception as e:
        if args.debug:
            import traceback
            traceback.print_exc()
        else:
            print(f"上传失败: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
