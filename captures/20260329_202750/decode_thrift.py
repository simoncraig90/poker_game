import json, base64, struct, sys

def decode_frames(jsonl_path):
    frames = []
    with open(jsonl_path, 'r') as f:
        for i, line in enumerate(f):
            obj = json.loads(line.strip())
            if obj.get('requestId') == '21444.113' and obj.get('opcode') == 2:
                raw = base64.b64decode(obj['payload'])
                frames.append((i, obj['direction'], obj['payloadLength'], raw))
    return frames

TYPE_NAMES = {
    0x00: 'STOP', 0x02: 'BOOL', 0x03: 'BYTE', 0x06: 'I16',
    0x08: 'I32', 0x0a: 'I64', 0x0b: 'STRING', 0x0c: 'STRUCT',
    0x0d: 'MAP', 0x0e: 'SET', 0x0f: 'LIST'
}

def decode_thrift_fields(data, offset, depth=0, max_depth=6):
    results = []
    indent = "  " * depth
    while offset < len(data):
        if data[offset] == 0x00:
            results.append(f"{indent}STOP (0x00) at offset {offset}")
            offset += 1
            break
        if offset + 3 > len(data):
            results.append(f"{indent}[TRUNCATED at offset {offset}, remaining: {data[offset:].hex()}]")
            break
        ftype = data[offset]
        fid = struct.unpack('>H', data[offset+1:offset+3])[0]
        offset += 3
        tname = TYPE_NAMES.get(ftype, f'UNKNOWN(0x{ftype:02x})')

        if ftype == 0x02:
            if offset < len(data):
                val = data[offset]
                results.append(f"{indent}Field {fid}: {tname} = {bool(val)} (0x{val:02x})")
                offset += 1
            else:
                results.append(f"{indent}Field {fid}: {tname} [TRUNCATED]")
                break
        elif ftype == 0x03:
            if offset < len(data):
                val = data[offset]
                signed = struct.unpack('>b', bytes([val]))[0]
                results.append(f"{indent}Field {fid}: {tname} = {signed} (0x{val:02x})")
                offset += 1
            else:
                results.append(f"{indent}Field {fid}: {tname} [TRUNCATED]")
                break
        elif ftype == 0x06:
            if offset + 2 <= len(data):
                val = struct.unpack('>h', data[offset:offset+2])[0]
                uval = struct.unpack('>H', data[offset:offset+2])[0]
                results.append(f"{indent}Field {fid}: I16 = {val} (unsigned: {uval}, 0x{data[offset:offset+2].hex()})")
                offset += 2
            else:
                results.append(f"{indent}Field {fid}: I16 [TRUNCATED]")
                break
        elif ftype == 0x08:
            if offset + 4 <= len(data):
                val = struct.unpack('>i', data[offset:offset+4])[0]
                uval = struct.unpack('>I', data[offset:offset+4])[0]
                results.append(f"{indent}Field {fid}: I32 = {val} (unsigned: {uval}, 0x{data[offset:offset+4].hex()})")
                offset += 4
            else:
                results.append(f"{indent}Field {fid}: I32 [TRUNCATED]")
                break
        elif ftype == 0x0a:
            if offset + 8 <= len(data):
                val = struct.unpack('>q', data[offset:offset+8])[0]
                results.append(f"{indent}Field {fid}: I64 = {val} (0x{data[offset:offset+8].hex()})")
                offset += 8
            else:
                results.append(f"{indent}Field {fid}: I64 [TRUNCATED]")
                break
        elif ftype == 0x0b:
            if offset + 4 <= len(data):
                slen = struct.unpack('>I', data[offset:offset+4])[0]
                offset += 4
                if offset + slen <= len(data):
                    sval = data[offset:offset+slen]
                    try:
                        text = sval.decode('utf-8')
                        results.append(f"{indent}Field {fid}: STRING[{slen}] = \"{text}\"")
                    except:
                        results.append(f"{indent}Field {fid}: STRING[{slen}] = (hex) {sval.hex()}")
                    offset += slen
                else:
                    results.append(f"{indent}Field {fid}: STRING[{slen}] [DATA TRUNCATED, avail={len(data)-offset}]")
                    offset = len(data)
                    break
            else:
                results.append(f"{indent}Field {fid}: STRING [TRUNCATED]")
                break
        elif ftype == 0x0c:
            results.append(f"{indent}Field {fid}: STRUCT {{")
            if depth < max_depth:
                sub, offset = decode_thrift_fields(data, offset, depth+1, max_depth)
                results.extend(sub)
            else:
                results.append(f"{indent}  [MAX DEPTH - skipping]")
                # rough skip
                stop_found = False
                while offset < len(data):
                    if data[offset] == 0x00:
                        offset += 1
                        stop_found = True
                        break
                    offset += 1
                if not stop_found:
                    break
            results.append(f"{indent}}}")
        elif ftype == 0x0f:
            if offset + 5 <= len(data):
                elem_type = data[offset]
                elem_count = struct.unpack('>I', data[offset+1:offset+5])[0]
                offset += 5
                elem_tname = TYPE_NAMES.get(elem_type, f'UNKNOWN(0x{elem_type:02x})')
                results.append(f"{indent}Field {fid}: LIST<{elem_tname}>[{elem_count}] {{")
                for li in range(elem_count):
                    if elem_type == 0x08:
                        if offset + 4 <= len(data):
                            val = struct.unpack('>i', data[offset:offset+4])[0]
                            results.append(f"{indent}  [{li}]: I32 = {val} (0x{data[offset:offset+4].hex()})")
                            offset += 4
                        else:
                            results.append(f"{indent}  [{li}]: [TRUNCATED]")
                            break
                    elif elem_type == 0x06:
                        if offset + 2 <= len(data):
                            val = struct.unpack('>h', data[offset:offset+2])[0]
                            uval = struct.unpack('>H', data[offset:offset+2])[0]
                            results.append(f"{indent}  [{li}]: I16 = {val} (unsigned: {uval})")
                            offset += 2
                        else:
                            results.append(f"{indent}  [{li}]: [TRUNCATED]")
                            break
                    elif elem_type == 0x0b:
                        if offset + 4 <= len(data):
                            slen2 = struct.unpack('>I', data[offset:offset+4])[0]
                            offset += 4
                            if offset + slen2 <= len(data):
                                try:
                                    text = data[offset:offset+slen2].decode('utf-8')
                                    results.append(f"{indent}  [{li}]: STRING = \"{text}\"")
                                except:
                                    results.append(f"{indent}  [{li}]: STRING = (hex) {data[offset:offset+slen2].hex()}")
                                offset += slen2
                            else:
                                results.append(f"{indent}  [{li}]: STRING [TRUNCATED]")
                                break
                        else:
                            results.append(f"{indent}  [{li}]: [TRUNCATED]")
                            break
                    elif elem_type == 0x0c:
                        results.append(f"{indent}  [{li}]: STRUCT {{")
                        if depth < max_depth:
                            sub, offset = decode_thrift_fields(data, offset, depth+2, max_depth)
                            results.extend(sub)
                        else:
                            results.append(f"{indent}    [MAX DEPTH]")
                            while offset < len(data) and data[offset] != 0x00:
                                offset += 1
                            if offset < len(data):
                                offset += 1
                        results.append(indent + "  }")
                    elif elem_type == 0x03:
                        if offset < len(data):
                            results.append(f"{indent}  [{li}]: BYTE = {data[offset]} (0x{data[offset]:02x})")
                            offset += 1
                        else:
                            results.append(f"{indent}  [{li}]: [TRUNCATED]")
                            break
                    else:
                        results.append(f"{indent}  [UNSUPPORTED elem type 0x{elem_type:02x} at offset {offset}]")
                        break
                results.append(indent + "}")
            else:
                results.append(f"{indent}Field {fid}: LIST [TRUNCATED]")
                break
        elif ftype == 0x0d:
            if offset + 6 <= len(data):
                ktype = data[offset]
                vtype = data[offset+1]
                mcount = struct.unpack('>I', data[offset+2:offset+6])[0]
                offset += 6
                ktname = TYPE_NAMES.get(ktype, f'0x{ktype:02x}')
                vtname = TYPE_NAMES.get(vtype, f'0x{vtype:02x}')
                results.append(f"{indent}Field {fid}: MAP<{ktname},{vtname}>[{mcount}] {{")
                for mi in range(mcount):
                    k_str = "?"
                    if ktype == 0x08:
                        if offset+4 <= len(data):
                            k_str = str(struct.unpack('>i', data[offset:offset+4])[0])
                            offset += 4
                        else: break
                    elif ktype == 0x0b:
                        if offset+4 <= len(data):
                            kl = struct.unpack('>I', data[offset:offset+4])[0]
                            offset += 4
                            k_str = data[offset:offset+kl].decode('utf-8','replace')
                            offset += kl
                        else: break
                    else:
                        results.append(f"{indent}  [UNSUPPORTED key type 0x{ktype:02x}]")
                        break

                    if vtype == 0x08:
                        if offset+4 <= len(data):
                            v = struct.unpack('>i', data[offset:offset+4])[0]
                            results.append(f"{indent}  {k_str} -> {v}")
                            offset += 4
                        else: break
                    elif vtype == 0x0b:
                        if offset+4 <= len(data):
                            vl = struct.unpack('>I', data[offset:offset+4])[0]
                            offset += 4
                            v = data[offset:offset+vl].decode('utf-8','replace')
                            results.append(f"{indent}  {k_str} -> \"{v}\"")
                            offset += vl
                        else: break
                    else:
                        results.append(f"{indent}  [UNSUPPORTED value type 0x{vtype:02x}]")
                        break
                results.append(indent + "}")
            else:
                results.append(f"{indent}Field {fid}: MAP [TRUNCATED]")
                break
        else:
            results.append(f"{indent}Field {fid}: UNKNOWN type 0x{ftype:02x} at offset {offset-3}")
            results.append(f"{indent}  context hex: {data[max(0,offset-3):offset+30].hex()}")
            break
    return results, offset


def analyze_frame(idx, direction, length, raw):
    print(f"\n{'='*80}")
    print(f"FRAME INDEX {idx} | {direction} | {length} bytes")
    print(f"Raw hex (first 100): {raw[:100].hex()}")

    framing = raw[0]
    msg_type = raw[1]
    print(f"Byte[0] framing: 0x{framing:02x}")
    print(f"Byte[1] message type: 0x{msg_type:02x}")
    print(f"Thrift payload starts at offset 2, {len(raw)-2} bytes")

    results, final_offset = decode_thrift_fields(raw, 2)
    for r in results:
        print(r)
    if final_offset < len(raw):
        print(f"[REMAINING {len(raw)-final_offset} unparsed bytes at offset {final_offset}: {raw[final_offset:final_offset+40].hex()}]")


def main():
    frames = decode_frames(r"C:\poker-research\captures\20260329_202750\websocket.jsonl")

    by_type = {}
    for idx, direction, length, raw in frames:
        if len(raw) >= 2:
            mt = raw[1]
            if mt not in by_type:
                by_type[mt] = []
            by_type[mt].append((idx, direction, length, raw))

    print("MESSAGE TYPE INDEX:")
    for mt in sorted(by_type.keys()):
        examples = by_type[mt]
        sizes = [e[2] for e in examples]
        dirs = [e[1] for e in examples]
        print(f"  0x{mt:02x}: {len(examples)} frames, dir={set(dirs)}, sizes: {sizes[:15]}{'...' if len(sizes)>15 else ''}")

    # 1. 0x6a table snapshot
    print("\n" + "#"*80)
    print("# 1. MESSAGE TYPE 0x6a (TABLE SNAPSHOT)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x6a, []):
        analyze_frame(idx, d, l, raw)

    # 2. 0x7d hand result
    print("\n" + "#"*80)
    print("# 2. MESSAGE TYPE 0x7d (HAND RESULT)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x7d, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 3. 0x6c player update
    print("\n" + "#"*80)
    print("# 3. MESSAGE TYPE 0x6c (PLAYER UPDATE)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x6c, [])[:5]:
        analyze_frame(idx, d, l, raw)

    # 4. 0x77 action
    print("\n" + "#"*80)
    print("# 4. MESSAGE TYPE 0x77 (ACTION)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x77, [])[:6]:
        analyze_frame(idx, d, l, raw)

    # 5. 0x76 card deal
    print("\n" + "#"*80)
    print("# 5. MESSAGE TYPE 0x76 (CARD DEAL)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x76, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 6. 0x78 pot update
    print("\n" + "#"*80)
    print("# 6. MESSAGE TYPE 0x78 (POT UPDATE)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x78, [])[:5]:
        analyze_frame(idx, d, l, raw)

    # 7. 0x73 hand start/state
    print("\n" + "#"*80)
    print("# 7. MESSAGE TYPE 0x73 (HAND START/STATE)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x73, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 8. 0x79 hole cards
    print("\n" + "#"*80)
    print("# 8. MESSAGE TYPE 0x79 (HOLE CARDS)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x79, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 9. 0x7b community cards
    print("\n" + "#"*80)
    print("# 9. MESSAGE TYPE 0x7b (COMMUNITY CARDS / BOARD)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x7b, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 10. 0x34 hand info
    print("\n" + "#"*80)
    print("# 10. MESSAGE TYPE 0x34 (HAND INFO)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x34, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 11. 0x83 and 0x6d
    print("\n" + "#"*80)
    print("# 11. MESSAGE TYPE 0x83 (HEARTBEAT PING) and 0x6d (ACK)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x83, [])[:3]:
        analyze_frame(idx, d, l, raw)
    for idx, d, l, raw in by_type.get(0x6d, [])[:3]:
        analyze_frame(idx, d, l, raw)

    # 12. 0x72 round/state transition
    print("\n" + "#"*80)
    print("# 12. MESSAGE TYPE 0x72 (ROUND/STATE TRANSITION)")
    print("#"*80)
    for idx, d, l, raw in by_type.get(0x72, [])[:5]:
        analyze_frame(idx, d, l, raw)


main()
