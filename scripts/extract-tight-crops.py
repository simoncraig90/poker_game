"""Extract tight hero+board card crops and label them for suit CNN training."""
import cv2, numpy as np, os, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

from advisor import find_table_region, crop_table
from ultralytics import YOLO
import unibet_card_detect as ucd

yolo = YOLO('vision/models/yolo_unibet.pt')

def find_tight_box(table_img, x1, y1, x2, y2):
    crop = table_img[y1:y2, x1:x2]
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 80)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None; best_a = 0
    for cnt in contours:
        a = cv2.contourArea(cnt)
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if a > h*w*0.15 and bw > w*0.3 and bh > h*0.3 and a > best_a:
            best_a = a; best = (bx, by, bw, bh)
    if best:
        bx,by,bw,bh = best
        return (x1+bx, y1+by, x1+bx+bw, y1+by+bh)
    px = int((x2-x1)*0.10); py = int((y2-y1)*0.08)
    return (x1+px, y1+py, x2-px, y2-py)

verified = {
    'client/unibet-verify.png': ['s','s'],
    'client/unibet-2c7h.png': ['c','h'],
    'client/unibet-check.png': ['h','s'],
    'vision/captures/live_5d_Qc.png': ['d','c'],
    'client/unibet-latest.png': ['c','c'],
    'client/unibet-playing.png': ['h','s'],
    'client/unibet-wrongcard.png': ['h','h'],
}

out_dir = 'vision/card_crops_unibet/tight_labeled'
os.makedirs(out_dir, exist_ok=True)

all_files = list(verified.keys()) + glob.glob('vision/captures/unibet/*.png') + glob.glob('client/unibet-table-*.png')
count = 0

for path in all_files:
    img = cv2.imread(path)
    if img is None: continue
    r = find_table_region(img)
    if not r: continue
    ti, _ = crop_table(img, r)
    th, tw = ti.shape[:2]
    res = yolo(ti, conf=0.4, verbose=False)

    for r2 in res:
        for b in r2.boxes:
            cls = int(b.cls[0])
            x1,y1,x2,y2 = [int(v) for v in b.xyxy[0]]
            tx1,ty1,tx2,ty2 = find_tight_box(ti, x1, y1, x2, y2)
            tight = ti[ty1:ty2, tx1:tx2]
            if tight.shape[0] < 30: continue

            suit = None
            fname = os.path.basename(path).replace('.png','')

            if cls == 0:  # board card
                card_id = ucd.identify_card(ti[y1:y2, x1:x2])
                if card_id != '??' and len(card_id) >= 2:
                    suit = card_id[1]
            elif cls == 1 and path in verified:
                hero_xs = sorted([int(bb.xyxy[0][0]) for bb in r2.boxes if int(bb.cls[0])==1])
                idx = hero_xs.index(x1) if x1 in hero_xs else -1
                if 0 <= idx < len(verified[path]):
                    suit = verified[path][idx]

            if suit:
                cv2.imwrite(os.path.join(out_dir, f'{suit}_{cls}_{fname}_{x1}.png'), tight)
                count += 1

sc = {s:0 for s in 'cdhs'}
for f in os.listdir(out_dir):
    if f[0] in sc: sc[f[0]] += 1
print(f'Total: {count}, Distribution: {sc}')
