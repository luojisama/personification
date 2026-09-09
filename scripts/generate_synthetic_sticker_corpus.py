"""Create deterministic, non-user visual fixtures for contract evaluation.

Manifest truth consists only of visible text, symbols and ordered displacement.
It deliberately does not make a model's emotional interpretation the answer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


# Ten contexts, five visibly different variants each.  The set includes the
# requested ironic image/text contrast, unclear OCR, comfort/refusal/closing.
CONTEXTS: tuple[dict[str, Any], ...] = (
    {"id":"greeting", "text":"嗨 你好", "symbol":"wave", "direction":"right", "intent":"问候"},
    {"id":"irony", "text":"我 很 开心", "symbol":"smile_cross", "direction":"left", "intent":"反讽画文冲突"},
    {"id":"comfort", "text":"抱抱 会好的", "symbol":"heart", "direction":"up", "intent":"安慰"},
    {"id":"refusal", "text":"不 行", "symbol":"cross", "direction":"left", "intent":"拒绝"},
    {"id":"closing", "text":"先这样 晚安", "symbol":"moon", "direction":"down", "intent":"结束话题"},
    {"id":"agreement", "text":"收到 OK", "symbol":"check", "direction":"right", "intent":"确认同意"},
    {"id":"surprise", "text":"啊？", "symbol":"burst", "direction":"up", "intent":"惊讶"},
    {"id":"apology", "text":"对不起", "symbol":"bow", "direction":"down", "intent":"道歉"},
    {"id":"uncertain_ocr", "text":"看 不 清", "symbol":"fog", "direction":"right", "intent":"OCR不确定", "uncertain":True},
    {"id":"celebration", "text":"太棒了!", "symbol":"star", "direction":"up", "intent":"庆祝"},
)
COLORS = ("#FFE08A", "#B9E4D0", "#FFB4B4", "#C7CEEA", "#D5F5E3")
SIZE = (320, 220)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    except OSError:
        return ImageFont.load_default()


def _symbol(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int) -> None:
    ink, width = "#1F2937", 6
    if kind == "wave":
        draw.arc((x-28,y-30,x+30,y+28),205,340,fill=ink,width=width); draw.line((x-20,y+18,x+22,y-10),fill=ink,width=width)
    elif kind == "smile_cross":
        draw.arc((x-30,y-5,x+30,y+35),190,350,fill=ink,width=width); draw.line((x-24,y-22,x-6,y-5),fill=ink,width=width); draw.line((x-6,y-22,x-24,y-5),fill=ink,width=width)
    elif kind == "heart":
        draw.polygon([(x,y+35),(x-35,y),(x-20,y-25),(x,y-5),(x+20,y-25),(x+35,y)],fill="#E05263")
    elif kind == "cross":
        draw.line((x-30,y-30,x+30,y+30),fill="#C0392B",width=width+2); draw.line((x+30,y-30,x-30,y+30),fill="#C0392B",width=width+2)
    elif kind == "moon":
        draw.ellipse((x-35,y-35,x+35,y+35),fill="#34495E"); draw.ellipse((x-17,y-42,x+42,y+18),fill="#FFFFFF")
    elif kind == "check":
        draw.line((x-35,y,x-8,y+28),fill="#1E8449",width=width+2); draw.line((x-8,y+28,x+42,y-30),fill="#1E8449",width=width+2)
    elif kind == "burst":
        for dx,dy in ((0,-40),(38,-14),(30,30),(-30,30),(-38,-14)): draw.line((x,y,x+dx,y+dy),fill="#D35400",width=width)
        draw.ellipse((x-12,y-12,x+12,y+12),fill="#F5B041")
    elif kind == "bow":
        draw.ellipse((x-24,y-15,x+24,y+30),outline=ink,width=width); draw.line((x-35,y+40,x+35,y+40),fill=ink,width=width)
    elif kind == "fog":
        for offset in (-20,0,20): draw.arc((x-38,y+offset-10,x+38,y+offset+18),180,360,fill="#7F8C8D",width=4)
    elif kind == "star":
        draw.regular_polygon((x,y,38),n_sides=5,rotation=0,fill="#F4D03F",outline=ink)


def _arrow(draw: ImageDraw.ImageDraw, direction: str) -> None:
    start,end={"right":((42,82),(88,82)),"left":((88,82),(42,82)),"up":((65,112),(65,58)),"down":((65,58),(65,112))}[direction]
    draw.line((*start,*end),fill="#2471A3",width=5); ex,ey=end
    if direction in {"right","left"}:
        sign=1 if direction=="right" else -1; draw.polygon([(ex,ey),(ex-12*sign,ey-8),(ex-12*sign,ey+8)],fill="#2471A3")
    else:
        sign=1 if direction=="down" else -1; draw.polygon([(ex,ey),(ex-8,ey-12*sign),(ex+8,ey-12*sign)],fill="#2471A3")


def _offset(direction: str, frame: int) -> tuple[int,int]:
    step=(-26,0,26)[frame]
    return {"right":(step,0),"left":(-step,0),"up":(0,-step),"down":(0,step)}[direction]


def _draw(context: dict[str,Any], variant: int, frame: int | None) -> Image.Image:
    image=Image.new("RGB",SIZE,COLORS[variant]); draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((8,8,312,212),radius=18,outline="#1F2937",width=3); _arrow(draw,str(context["direction"]))
    dx,dy=_offset(str(context["direction"]),frame if frame is not None else 1); _symbol(draw,str(context["symbol"]),190+dx,94+dy)
    if context.get("uncertain"):
        draw.text((118,160),str(context["text"]),fill="#5D6D7E",font=_font(12)); draw.text((120,162),"?",fill="#95A5A6",font=_font(12))
    else:
        draw.text((28,158),str(context["text"]),fill="#111827",font=_font(27))
    draw.text((28,28),f"{context['id']} #{variant+1}",fill="#34495E",font=_font(13)); return image


def _expected(context: dict[str,Any], dynamic: bool) -> dict[str,Any]:
    objective: dict[str,Any]={
        "ocr_any_of":[] if context.get("uncertain") else [str(context["text"])],
        "visible_symbol":context["symbol"], "clarity":"uncertain" if context.get("uncertain") else "clear",
        "required_output_fields":["summary","description","ocr_text","subject_action","visual_confidence"],
    }
    if dynamic:
        direction=str(context["direction"])
        objective.update({"frame_count":3,"motion_direction":direction,"motion_any_of":{"right":["右","向右"],"left":["左","向左"],"up":["上","向上"],"down":["下","向下"]}[direction]})
    return {"objective":objective,"context_layer":str(context["intent"])}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    root=args.output.resolve(); root.mkdir(parents=True,exist_ok=True); rows=[]
    for context_index,context in enumerate(CONTEXTS):
        for variant in range(5):
            number=context_index*5+variant+1; dynamic=variant>=2; image_name=f"synthetic-{number:02d}.{'gif' if dynamic else 'png'}"; path=root/image_name
            if dynamic:
                frames=[_draw(context,variant,frame) for frame in range(3)]; frames[0].save(path,save_all=True,append_images=frames[1:],duration=[140,180,220],loop=0)
            else: _draw(context,variant,None).save(path)
            rows.append({"id":f"synthetic-{number:02d}","image":image_name,"kind":"dynamic" if dynamic else "static","synthetic":True,"stratum":context["id"],"expected":_expected(context,dynamic)})
    (root/"manifest.json").write_text(json.dumps({"version":2,"source":"deterministic synthetic pixels; objective visible facts only","cases":rows},ensure_ascii=False,indent=2),encoding="utf-8")
    print(root/"manifest.json"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
