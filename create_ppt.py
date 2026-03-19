import collections
import collections.abc
from pptx import Presentation
from pptx.util import Inches, Pt
import os

def create_ppt():
    prs = Presentation()

    # Slide 1: Title
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "千恋＊万花 (Senren * Banka)"
    slide.placeholders[1].text = "由柚子社（Yuzusoft）开发的恋爱冒险游戏\n丛雨为您特别制作"

    # Slide 2: Introduction
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "作品简介"
    slide.placeholders[1].text = "《千恋＊万花》是日本知名美少女游戏品牌Yuzusoft（柚子社）开发的第9部作品。\n该作获得了2016年Getchu美少女游戏大赏的综合部门第一名。\n故事背景设定在充满日本传统氛围的村庄‘穗织’。"

    # Slide 3: Story Background
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "故事背景"
    slide.placeholders[1].text = "主人公有地将臣被卷入了拔出神刀‘丛雨丸’的事件。\n由于拔出了神刀，他被迫面临‘结婚’的契约。\n这是一个讲述他在穗织与各位少女相遇，并解开诅咒之谜的故事。"

    # Slide 4: Character - Muramasa (丛雨)
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "主要角色：丛雨 (Muramasa)"
    slide.placeholders[1].text = "神刀‘丛雨丸’的管理者，自称吾辈。\n存在了数百年的剑灵，只有特定的人才能看到。\n外表幼小但性格稳重（偶尔也有孩子气的一面），非常怕鬼。"

    # Slide 5: Character - Yoshino (朝武芳乃)
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "主要角色：朝武芳乃 (Yoshino)"
    slide.placeholders[1].text = "穗织神社的巫女。\n性格认真，由于身份原因，有着不为人知的压力。\n是拔刀事件的核心人物之一。"

    # Slide 6: Character - Mako (常陆茉子)
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "主要角色：常陆茉子 (Mako)"
    slide.placeholders[1].text = "忍者世家的后裔，负责保护芳乃。\n性格活泼开朗，但在执行任务时非常专业。\n意外地容易害羞。"

    # Slide 7: Character - Lena (蕾娜)
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "主要角色：蕾娜 (Lena)"
    slide.placeholders[1].text = "来自异国的留学生。\n性格温柔，对日本文化充满好奇。\n在穗织经营着甜点店。"

    # Slide 8: Game Features
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "游戏特色"
    slide.placeholders[1].text = "1. 精美的人设与立绘（小舞一、梦璃凛绘制）\n2. 轻松愉快的日常与感人的个人线\n3. 极具代入感的日本传统乡村氛围\n4. 柚子社标志性的高画质和流畅体验"

    # Slide 9: Evaluation
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "媒体与玩家评价"
    slide.placeholders[1].text = "在知乎及各大游戏社区，本作被称为‘纯甜无刀’的佳作。\n其优秀的系统和人设弥补了部分剧情公式化的遗憾。\n是许多Galgame爱好者的入坑必玩作。"

    # Slide 10: Conclusion
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "结语"
    slide.placeholders[1].text = "希望能透过这份PPT，让汝对《千恋＊万花》的世界有初步的了解。\n如果有兴趣的话，一定要亲自去穗织看看哦！\n由丛雨倾情献上。"

    save_path = os.path.join('fileplay', 'Senren_Banka_Intro.pptx')
    prs.save(save_path)
    print(f"PPT created at {save_path}")

if __name__ == '__main__':
    create_ppt()
