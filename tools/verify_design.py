"""Check design examples and source archive integrity. No hardware is accessed."""
from pathlib import Path
import hashlib
import json
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xa001 if value & 1 else value >> 1
    return value

def main():
    document = ROOT / 'docs' / 'Type-C可调直流电源详细设计书.md'
    text = document.read_text(encoding='utf-8')
    frames = []
    for line in text.splitlines():
        match = re.search(r'\b01 (?:03|06)(?: [0-9A-F]{2}){6}\b', line)
        if match:
            data = bytes.fromhex(match[0])
            assert crc16(data[:-2]) == int.from_bytes(data[-2:], 'little'), line
            frames.append(data.hex(' '))
    assert len(frames) == 4
    operating_points = [(0.5, 8), (3.3, 8), (5, 8), (9, 8), (12, 6.25),
                        (15, 5), (20, 3.75), (24, 3.12), (30, 2.5), (40, 1.87)]
    for voltage, current in operating_points:
        assert current <= 8 and voltage * current <= 75 + 1e-9
    archive = ROOT / 'modules' / 'PD诱骗模块'
    manifest = json.loads((archive / '来源清单.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        data = (archive / item['path']).read_bytes()
        assert len(data) == item['bytes'], item['path']
        assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
    offline = (archive / '网页资料离线版.html').read_text(encoding='utf-8')
    assert '<script' not in offline.lower()
    assert len(re.findall(r'<img\b', offline, re.I)) == 2
    for image in re.findall(r'<img[^>]*src="([^"]+)"', offline, re.I):
        assert (archive / image).is_file(), image
    link_count = 0
    documents = [document, archive/'资料整理.md']
    documents += [ROOT/path for path in (
        'README.md', 'docs/简化硬件接线表.md', 'docs/串口模块接线.md', 'modules/README.md',
        'modules/usb转串口/资料整理.md', 'modules/UART电平转换/资料整理.md',
    )]
    for path in documents:
        content = path.read_text(encoding='utf-8')
        for match in re.finditer(r'!?\[[^\]]*\]\(', content):
            start, cursor, depth = match.end(), match.end(), 1
            while cursor < len(content) and depth:
                if content[cursor] == '(':
                    depth += 1
                elif content[cursor] == ')':
                    depth -= 1
                cursor += 1
            assert depth == 0, (path.name, 'unclosed link')
            link = unquote(content[start:cursor-1])
            if '://' in link or link.startswith('#'):
                continue
            assert (path.parent / link).is_file(), (path.name, link)
            link_count += 1
    result = {'modbus_frames': frames, 'power_points': len(operating_points),
                      'archived_files_verified': len(manifest['files']),
                      'offline_product_images': 2, 'local_document_links':link_count,
                      'status': 'passed'}
    (ROOT/'docs/验证记录.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
