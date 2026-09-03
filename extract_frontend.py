#!/usr/bin/env python3
"""
SpotiFLAC-Next Embedded Frontend Extractor
Extracts the complete production React/Tailwind/Radix web UI directly from
the SpotiFLAC-Next Go binary (embed.FS) or AppImage.
"""

import os
import sys
import struct
import shutil
import subprocess
import tempfile

def find_embed_files(elf_data):
    """
    Locates Go embed.FS entries for frontend/dist inside an ELF binary.
    """
    needle = b'frontend/dist/index.html'
    p = elf_data.find(needle)
    if p == -1:
        raise ValueError("frontend/dist/index.html string not found in binary")

    # In 64-bit Linux ELF with default load base (0x400000):
    # virtual address = file_offset + 0x400000
    vaddr = p + 0x400000
    packed_vaddr = struct.pack('<Q', vaddr)
    pos = elf_data.find(packed_vaddr)
    if pos == -1:
        raise ValueError("Pointer to frontend/dist/index.html not found")

    file_entry_size = 48  # sizeof(embed.file): 8+8+8+8+16
    curr = pos

    # Walk backward to find the beginning of the embed.file table
    while curr >= file_entry_size:
        name_ptr, name_len, data_ptr, data_len = struct.unpack_from('<QQQQ', elf_data, curr - file_entry_size)
        if name_ptr >= 0x400000 and name_ptr < 0x3000000 and 0 < name_len < 300:
            name_offset = name_ptr - 0x400000
            name = elf_data[name_offset:name_offset+name_len].decode('latin1', errors='ignore')
            if name.startswith('frontend/dist'):
                curr -= file_entry_size
                continue
        break

    start_array = curr
    files = []

    # Walk forward to collect all files
    while curr + file_entry_size <= len(elf_data):
        name_ptr, name_len, data_ptr, data_len = struct.unpack_from('<QQQQ', elf_data, curr)
        if not (name_ptr >= 0x400000 and name_ptr < 0x3000000 and 0 < name_len < 300):
            break
        name_offset = name_ptr - 0x400000
        name = elf_data[name_offset:name_offset+name_len].decode('latin1', errors='ignore')
        if not name.startswith('frontend/dist'):
            break

        rel_path = name[len('frontend/dist'):].lstrip('/')
        data_offset = data_ptr - 0x400000
        file_bytes = elf_data[data_offset:data_offset+data_len]

        files.append((rel_path, file_bytes))
        curr += file_entry_size

    return files

def extract_from_binary(binary_path, target_dir, shim_source=None):
    """
    Extracts embedded files from an ELF binary directly to target_dir.
    """
    print(f"[Extractor] Reading binary from {binary_path}...")
    with open(binary_path, 'rb') as f:
        elf_data = f.read()

    files = find_embed_files(elf_data)
    print(f"[Extractor] Found {len(files)} embedded files for frontend/dist.")

    os.makedirs(target_dir, exist_ok=True)

    # Remove previous assets directory to prevent mixing old and new hashed bundles
    assets_dir = os.path.join(target_dir, "assets")
    if os.path.exists(assets_dir):
        print(f"[Extractor] Cleaning previous asset chunks in {assets_dir}...")
        shutil.rmtree(assets_dir, ignore_errors=True)

    count = 0

    for rel_path, file_bytes in files:
        if not rel_path:
            continue
        dest_path = os.path.join(target_dir, rel_path)
        if len(file_bytes) == 0 and not '.' in os.path.basename(rel_path):
            os.makedirs(dest_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, 'wb') as out_f:
                out_f.write(file_bytes)
            count += 1

    # Inject wails-browser-shim.js into index.html
    index_path = os.path.join(target_dir, "index.html")
    if os.path.isfile(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            html = f.read()

        shim_tag = '<script src="/wails-browser-shim.js"></script>'
        if shim_tag not in html:
            # Inject before the first <script
            pos = html.find('<script')
            if pos != -1:
                html = html[:pos] + shim_tag + "\n    " + html[pos:]
            else:
                html = html.replace('</head>', f'  {shim_tag}\n  </head>')

            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(html)
            print("[Extractor] Injected wails-browser-shim.js into index.html")

    # Copy shim into target_dir if provided
    if shim_source and os.path.isfile(shim_source):
        shutil.copy2(shim_source, os.path.join(target_dir, "wails-browser-shim.js"))
        print(f"[Extractor] Copied {shim_source} -> {target_dir}/wails-browser-shim.js")

    print(f"[Extractor] Successfully extracted {count} files to {target_dir}")
    return count

def extract_from_appimage(appimage_path, target_dir, shim_source=None):
    """
    Extracts the binary from an AppImage and then extracts its frontend assets.
    """
    print(f"[Extractor] Processing AppImage: {appimage_path}...")
    temp_dir = tempfile.mkdtemp(prefix="spotiflac_extract_")
    try:
        bin_path = None
        # Try 7z extraction first
        try:
            res = subprocess.run(
                ["7z", "e", "-y", appimage_path, "usr/bin/SpotiFLAC-Next", f"-o{temp_dir}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            candidate = os.path.join(temp_dir, "SpotiFLAC-Next")
            if res.returncode == 0 and os.path.isfile(candidate):
                bin_path = candidate
        except Exception:
            pass

        # Try appimage-extract if 7z didn't succeed
        if not bin_path:
            try:
                subprocess.run(
                    [appimage_path, "--appimage-extract", "usr/bin/SpotiFLAC-Next"],
                    cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                candidate = os.path.join(temp_dir, "squashfs-root", "usr", "bin", "SpotiFLAC-Next")
                if os.path.isfile(candidate):
                    bin_path = candidate
            except Exception:
                pass

        if not bin_path or not os.path.isfile(bin_path):
            raise RuntimeError(f"Could not extract usr/bin/SpotiFLAC-Next from {appimage_path}")

        return extract_from_binary(bin_path, target_dir, shim_source)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    if len(sys.argv) < 3:
        print("Usage: extract_frontend.py <path_to_SpotiFLAC-Next_or_AppImage> <target_web_dir> [path_to_shim.js]")
        sys.exit(1)

    source_path = sys.argv[1]
    target_dir = sys.argv[2]
    shim_source = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "wails-browser-shim.js")

    if not os.path.exists(source_path):
        print(f"[Error] Source path not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    # Check if input is ELF or AppImage
    with open(source_path, 'rb') as f:
        header = f.read(1024)

    if header.startswith(b'\x7fELF'):
        # Check if SquashFS is embedded (AppImage)
        if b'hsqs' in header or b'AI\x02' in header[:16]:
            extract_from_appimage(source_path, target_dir, shim_source)
        else:
            extract_from_binary(source_path, target_dir, shim_source)
    else:
        extract_from_appimage(source_path, target_dir, shim_source)

if __name__ == '__main__':
    main()
