import sys
from pptx import Presentation
p = Presentation(sys.argv[1])
want = [int(a) for a in sys.argv[2:]] or range(1, len(p.slides) + 1)
def walk(shapes, d=0):
    for sh in shapes:
        if sh.shape_type == 6:
            print("  " * d + f"  #{sh.shape_id} GROUP {sh.name}")
            walk(sh.shapes, d + 1)
            continue
        if sh.has_text_frame and sh.text_frame.text.strip():
            paras = [pp for pp in sh.text_frame.paragraphs]
            ptxt = " | ".join(f"[{len(pp.runs)}r]{''.join(r.text for r in pp.runs)}" for pp in paras)
            print("  " * d + f"  #{sh.shape_id} {sh.name} w={sh.width/914400:.2f} h={sh.height/914400:.2f} :: {ptxt}")
        elif sh.shape_type in (3, 19):
            print("  " * d + f"  #{sh.shape_id} {sh.name} [{sh.shape_type}]")
for n in want:
    s = p.slides[n - 1]
    print(f"=== slide {n} ({s.slide_layout.name})")
    walk(s.shapes)
