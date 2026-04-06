"""Train suit CNN on tight card crops and test on all verified hero images."""
import os, sys, random, cv2, numpy as np, torch, torch.nn as nn
from torch.utils.data import Dataset, DataLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'vision'))

SUITS = ['c','d','h','s']
S2I = {s:i for i,s in enumerate(SUITS)}

class DS(Dataset):
    def __init__(self, s, a=False): self.s=s; self.a=a
    def __len__(self): return len(self.s)
    def __getitem__(self, i):
        p,si=self.s[i]; img=cv2.imread(p)
        if img is None: img=np.zeros((64,48,3),dtype='uint8')
        img=cv2.resize(img,(48,64))
        if self.a:
            if random.random()>0.5: img=np.clip(img*random.uniform(0.6,1.4),0,255).astype('uint8')
            if random.random()>0.5: img=cv2.flip(img,1)
            if random.random()>0.5:
                M=np.float32([[1,0,random.randint(-3,3)],[0,1,random.randint(-3,3)]])
                img=cv2.warpAffine(img,M,(48,64))
        return torch.tensor(np.transpose(img.astype(np.float32)/255.0,(2,0,1))),si

class SuitCNN(nn.Module):
    def __init__(s):
        super().__init__()
        s.f=nn.Sequential(nn.Conv2d(3,32,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
                          nn.Conv2d(32,64,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
                          nn.Conv2d(64,128,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
                          nn.AdaptiveAvgPool2d((4,3)))
        s.c=nn.Sequential(nn.Flatten(),nn.Linear(128*12,256),nn.ReLU(),nn.Dropout(0.4),nn.Linear(256,4))
    def forward(s,x): return s.c(s.f(x))

# Load data
data_dir = 'vision/card_crops_unibet/tight_labeled'
sa = [(os.path.join(data_dir,f), S2I[f[0]]) for f in os.listdir(data_dir) if f.endswith('.png') and f[0] in S2I]
sc = {s:0 for s in SUITS}
for _,si in sa: sc[SUITS[si]] += 1
print(f'Total: {len(sa)}, Dist: {sc}')

random.shuffle(sa)
sp = max(int(len(sa)*0.85), len(sa)-20)
tdl = DataLoader(DS(sa[:sp]*30, True), batch_size=32, shuffle=True)
vdl = DataLoader(DS(sa[sp:]), batch_size=32)
print(f'Train: {len(sa[:sp])*30}, Val: {len(sa[sp:])}')

dev = torch.device('cuda')
m = SuitCNN().to(dev)
opt = torch.optim.Adam(m.parameters(), lr=0.001)
crit = nn.CrossEntropyLoss()
sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 150)
best = 0

for ep in range(150):
    m.train(); c=t=0
    for imgs,labels in tdl:
        imgs,labels=imgs.to(dev),labels.to(dev)
        o=m(imgs);l=crit(o,labels);opt.zero_grad();l.backward();opt.step()
        _,p=o.max(1);c+=p.eq(labels).sum().item();t+=labels.size(0)
    sch.step()
    m.eval();vc=vt=0
    with torch.no_grad():
        for imgs,labels in vdl:
            imgs,labels=imgs.to(dev),labels.to(dev)
            _,p=m(imgs).max(1);vc+=p.eq(labels).sum().item();vt+=labels.size(0)
    va=vc/max(vt,1)
    if va>=best: best=va; torch.save(m.state_dict(),'vision/models/suit_cnn_unibet.pt')
    if (ep+1)%50==0: print(f'Ep {ep+1}: t={c/t:.3f} v={va:.3f} b={best:.3f}')

print(f'Best: {best:.3f}')

# Test on hero cards
m.load_state_dict(torch.load('vision/models/suit_cnn_unibet.pt', weights_only=True)); m.eval()

from advisor import find_table_region, crop_table
from ultralytics import YOLO

def find_tight_box(table_img, x1, y1, x2, y2):
    crop = table_img[y1:y2, x1:x2]
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 80)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_r = None; best_a = 0
    for cnt in contours:
        a = cv2.contourArea(cnt)
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if a > h*w*0.15 and bw > w*0.3 and bh > h*0.3 and a > best_a:
            best_a = a; best_r = (bx, by, bw, bh)
    if best_r:
        bx,by,bw,bh = best_r
        return (x1+bx, y1+by, x1+bx+bw, y1+by+bh)
    px=int((x2-x1)*0.10); py=int((y2-y1)*0.08)
    return (x1+px, y1+py, x2-px, y2-py)

yolo = YOLO('vision/models/yolo_unibet.pt')

print('\n=== Hero Test ===')
all_ok = True
for name,path,exp in [('Js_Ks','client/unibet-verify.png',['s','s']),
                       ('2c_7h','client/unibet-2c7h.png',['c','h']),
                       ('3h_Ts','client/unibet-check.png',['h','s']),
                       ('5d_Ac','vision/captures/live_5d_Qc.png',['d','c']),
                       ('Ac_6c','client/unibet-latest.png',['c','c']),
                       ('9h_As','client/unibet-playing.png',['h','s']),
                       ('5h_Kh','client/unibet-wrongcard.png',['h','h'])]:
    img=cv2.imread(path); r=find_table_region(img)
    if not r: continue
    ti,_=crop_table(img,r); th,tw=ti.shape[:2]
    res=yolo(ti,conf=0.4,verbose=False)
    hb=sorted([(int(b.xyxy[0][0]),int(b.xyxy[0][1]),int(b.xyxy[0][2]),int(b.xyxy[0][3]))
               for r2 in res for b in r2.boxes if int(b.cls[0])==1],key=lambda x:x[0])
    ds=[]
    for bx in hb[:2]:
        tx1,ty1,tx2,ty2=find_tight_box(ti,*bx)
        tight=ti[ty1:ty2,tx1:tx2]
        tight_r=cv2.resize(tight,(48,64)).astype(np.float32)/255.0
        with torch.no_grad():
            _,pred=m(torch.tensor(np.transpose(tight_r,(2,0,1))).unsqueeze(0).to(dev)).max(1)
            suit=SUITS[pred.item()]
            # Color sanity check
            hsv=cv2.cvtColor(tight,cv2.COLOR_BGR2HSV)
            red=cv2.inRange(hsv,(0,50,50),(15,255,255))|cv2.inRange(hsv,(155,50,50),(180,255,255))
            is_red=cv2.countNonZero(red)/max(red.size,1)>0.04
            if is_red and suit in 'cs': suit='h'
            elif not is_red and suit in 'dh': suit='s'
            ds.append(suit)
    ok=ds==exp
    if not ok: all_ok=False
    print(f'{name}: {ds} exp={exp} {"OK" if ok else "FAIL"}')

print(f'\n{"ALL PASS" if all_ok else "FAILURES"}')
