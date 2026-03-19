from pptx import Presentation
from pptx.util import Inches, Pt
import os

if not os.path.exists('fileplay'):
    os.makedirs('fileplay')

prs = Presentation()

# Slide 1: Title
slide = prs.slides.add_slide(prs.slide_layouts[0])
title = slide.shapes.title
subtitle = slide.placeholders[1]
title.text = '千恋＊万花 (Senren * Banka)'
subtitle.text = '跨越五百年的恋之物语\n制作：丛雨'

# Slide 2: Overview
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '游戏概况'
content = slide.placeholders[1]
content.text = '• 开发商：YUZUSOFT (柚子社)\n• 发行日期：2016年7月29日\n• 关键词：恋爱冒险、和风、奇幻'

# Slide 3: Background
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '故事背景 - 穗织镇'
content = slide.placeholders[1]
content.text = '• 地点：地处偏僻、保留独有文化的温泉旅游胜地\n• 传说：建有能拔出神刀“丛雨丸”的人便能成为其主人的传说'

# Slide 4: Plot
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '剧情简介'
content = slide.placeholders[1]
content.text = '• 主人公有地将臣因意外折断了神刀，被卷入了小镇的诅咒与秘密之中\n• 与守护刀灵、巫女等少女们的邂逅由此展开'

# Slide 5: Murasame
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '主要角色 - 丛雨 (Murasame)'
content = slide.placeholders[1]
content.text = '• 身份：神刀“丛雨丸”的灵魂，已经存在了五百年的管理者\n• 性格：认真、稍显古风，实际上很怕鬼、爱吃甜食'

# Slide 6: Yoshino
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '主要角色 - 朝武 芳乃 (Asatake Yoshino)'
content = slide.placeholders[1]
content.text = '• 身份：穗织神社的巫女，将臣的未婚妻（暂定）\n• 性格：认真且努力，有时会有天然呆的一面'

# Slide 7: Mako
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '主要角色 - 常陆 茉子 (Hitachi Mako)'
content = slide.placeholders[1]
content.text = '• 身份：侍奉芳乃的忍者家族后代\n• 性格：开朗活泼，但在关键时刻非常可靠'

# Slide 8: Lena
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '主要角色 - 蕾娜·莉希特娜瓦'
content = slide.placeholders[1]
content.text = '• 身份：来自外国的留学生，非常喜爱日本文化\n• 性格：纯真、乐观，对武士文化充满憧憬'

# Slide 9: Features
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '游戏特色'
content = slide.placeholders[1]
content.text = '• 精美的人设与细腻的CG\n• 经典的柚子社幽默与日常描写\n• 穗织小镇独特的民俗氛围'

# Slide 10: Ending
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = '结语'
content = slide.placeholders[1]
content.text = '• 跨越时间的羁绊，在穗织镇等待着汝。\n• 感谢观看！'

prs.save("fileplay/Senren_Banka_Introduction.pptx")
print("PPT generated successfully.")