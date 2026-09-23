"""端到端冒烟测试：用 Flask test client + 真实 Gemini API 跑一遍主要流程。

用法：set -a; source .env.dev; set +a; .venv/bin/python scripts/smoke_test.py
会在 DATABASE_PATH 指向的库里创建测试用户 smoke_tester（仅限本地开发库）。
"""

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_chat_site import create_app  # noqa: E402
from ai_chat_site.db import get_db  # noqa: E402
from ai_chat_site.models import User  # noqa: E402

assert "dev" in os.environ.get("DATABASE_PATH", ""), "只允许在本地开发库上运行"

app = create_app()
with app.app_context():
    if not User.get_by_username_or_email("smoke_tester"):
        User.create("smoke_tester", "smoke@example.com", "smoke-pass-123456")

c = app.test_client()
H = {"Origin": "http://localhost", "Host": "localhost"}
BASE = "http://localhost"


def ok(cond, label, extra=""):
    print(("✅" if cond else "❌"), label, extra)
    if not cond:
        global failed
        failed += 1


failed = 0

page = c.get("/auth/login", base_url=BASE)
token = page.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
r = c.post("/auth/login", data={"identifier": "smoke_tester", "password": "smoke-pass-123456", "csrf_token": token}, base_url=BASE)
ok(r.status_code == 302, "登录")

r = c.get("/chat", base_url=BASE)
ok(r.status_code == 200 and b"bootData" in r.data, "聊天页渲染")
csp = r.headers.get("Content-Security-Policy", "")
ok("cdn.jsdelivr.net" in csp, "CSP 允许 jsdelivr")

conv = c.post("/api/conversations", json={"title": "新对话"}, headers=H, base_url=BASE).get_json()["id"]


def stream(payload):
    t = time.time()
    r = c.post("/api/chat/stream", json=payload, headers=H, base_url=BASE, buffered=True)
    events = []
    for block in r.data.decode().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return r.status_code, events, time.time() - t


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


# 1. 基础对话 + 思考 + 自动标题
code, ev, dt = stream({"conversation_id": conv, "message": "用一句话解释什么是光合作用", "model": "gemini-3.8-flash", "thinking": "medium"})
types = [e["type"] for e in ev]
ok(code == 200 and "done" in types and text_of(ev), "流式对话 3.8 Flash", f"{dt:.1f}s 事件={sorted(set(types))}")
ok("title" in types, "自动生成标题", next((e["title"] for e in ev if e["type"] == "title"), ""))

# 2. 联网搜索
code, ev, dt = stream({"conversation_id": conv, "message": "今天北京天气怎么样？", "model": "gemini-3.8-flash", "tools": ["search"]})
g = next((e for e in ev if e["type"] == "grounding"), None)
ok(g and g["sources"], "联网搜索带来源", f"{len(g['sources']) if g else 0} 条来源 {dt:.1f}s")

# 3. 多轮上下文
code, ev, dt = stream({"conversation_id": conv, "message": "我第一个问题问的是什么？只回答主题", "model": "gemini-3.5-flash-lite"})
ok("光合" in text_of(ev), "多轮上下文记忆", text_of(ev)[:40])

# 4. 重新生成
code, ev, dt = stream({"conversation_id": conv, "regenerate": True, "model": "gemini-3.5-flash-lite"})
msgs = c.get(f"/api/conversations/{conv}/messages", base_url=BASE).get_json()["messages"]
ok(code == 200 and len(msgs) == 6 and msgs[-1]["role"] == "model", "重新生成（不重复插入）", f"消息数={len(msgs)}")

# 5. 编辑第一条消息
first_user = msgs[0]["id"]
code, ev, dt = stream({"conversation_id": conv, "message": "用一句话解释什么是蒸腾作用", "truncate_from": first_user, "model": "gemini-3.5-flash-lite"})
msgs = c.get(f"/api/conversations/{conv}/messages", base_url=BASE).get_json()["messages"]
ok(len(msgs) == 2 and "蒸腾" in msgs[0]["content"], "编辑后截断重发", f"消息数={len(msgs)}")

# 6. 上传 PDF（原生理解）
from pypdf import PdfWriter  # noqa: E402

buf = io.BytesIO()
w = PdfWriter()
for _ in range(3):
    w.add_blank_page(200, 200)
w.write(buf)
r = c.post("/api/upload", data={"file": (io.BytesIO(buf.getvalue()), "测试文档.pdf")}, headers=H, base_url=BASE, content_type="multipart/form-data")
fid = r.get_json().get("id")
ok(r.status_code == 200 and fid, "上传中文名 PDF", r.get_json().get("name", ""))
code, ev, dt = stream({"conversation_id": conv, "message": "这个 PDF 有几页？只回答数字", "file_ids": [fid], "model": "gemini-3.8-flash"})
ok("3" in text_of(ev), "PDF 原生理解", text_of(ev)[:30])

# 7. 上传 Excel（文字提取）
from openpyxl import Workbook  # noqa: E402

wb = Workbook()
ws = wb.active
ws.append(["产品", "单价"])
ws.append(["绿茶", 88])
ws.append(["红茶", 120])
xb = io.BytesIO()
wb.save(xb)
r = c.post("/api/upload", data={"file": (io.BytesIO(xb.getvalue()), "价格表.xlsx")}, headers=H, base_url=BASE, content_type="multipart/form-data")
xid = r.get_json().get("id")
code, ev, dt = stream({"conversation_id": conv, "message": "表格里哪个产品最贵？只回答产品名", "file_ids": [xid], "model": "gemini-3.5-flash-lite"})
ok("红茶" in text_of(ev), "Excel 解析", text_of(ev)[:30])

# 8. 代码执行
code, ev, dt = stream({"conversation_id": conv, "message": "用 Python 计算 2 的 100 次方", "tools": ["code"], "model": "gemini-3.8-flash"})
ok("1267650600228229401496703205376" in text_of(ev).replace(",", ""), "代码执行", f"{dt:.1f}s")

# 9. 图片生成 + 多轮改图
conv2 = c.post("/api/conversations", json={}, headers=H, base_url=BASE).get_json()["id"]
code, ev, dt = stream({"conversation_id": conv2, "message": "一片薄荷叶，白底，极简插画", "model": "gemini-3.1-flash-image", "aspect_ratio": "1:1"})
imgs = [e for e in ev if e["type"] == "image"]
ok(imgs, "图片生成", f"{len(imgs)} 张 {dt:.1f}s")
if imgs:
    r = c.get(imgs[0]["file"]["url"], base_url=BASE)
    ok(r.status_code == 200 and r.mimetype.startswith("image/"), "生成图片可访问", r.mimetype)
code, ev, dt = stream({"conversation_id": conv2, "message": "把叶子改成天蓝色", "model": "gemini-3.1-flash-image"})
ok([e for e in ev if e["type"] == "image"], "多轮改图", f"{dt:.1f}s")

# 9b. OCR（需要 Pillow 生成测试图，未安装则跳过）
try:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 200), "white")
    font_path = next((p for p in ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"] if os.path.exists(p)), None)
    font = ImageFont.truetype(font_path, 40) if font_path else ImageFont.load_default()
    ImageDraw.Draw(img).text((30, 60), "报价单 No.2026-0923 红茶 120元", fill="black", font=font)
    ib = io.BytesIO()
    img.save(ib, "PNG")
    r = c.post("/api/upload", data={"file": (io.BytesIO(ib.getvalue()), "ocr.png")}, headers=H, base_url=BASE, content_type="multipart/form-data")
    j = c.post(f"/api/files/{r.get_json()['id']}/ocr", headers=H, base_url=BASE).get_json()
    ok("2026-0923" in (j.get("text") or ""), "OCR 识别图片文字", repr((j.get("text") or "")[:40]))
except ImportError:
    print("⏭  跳过 OCR（未安装 Pillow）")

# 10. 3.1 Pro 深度思考
code, ev, dt = stream({"conversation_id": conv, "message": "9.11 和 9.9 哪个大？", "model": "gemini-3.1-pro-preview", "thinking": "high"})
ok(any(e["type"] == "thought" for e in ev) and text_of(ev), "3.1 Pro 深度思考（含思考摘要）", f"{dt:.1f}s")

# 11. 2.5 Pro（旧版思考参数）
code, ev, dt = stream({"conversation_id": conv, "message": "说声你好", "model": "gemini-2.5-pro", "thinking": "low"})
ok(text_of(ev), "2.5 Pro 兼容", f"{dt:.1f}s")

# 12. 记忆
time.sleep(1)
with app.app_context():
    n = get_db().execute("SELECT COUNT(*) FROM memory_items WHERE embed_model='gemini-embedding-001'").fetchone()[0]
ok(n > 0, "记忆向量写入", f"{n} 条")

# 13. 搜索 / 导出 / 统计
r = c.get("/api/conversations?q=蒸腾", base_url=BASE).get_json()
ok(any(x["id"] == conv for x in r["conversations"]), "对话全文搜索")
r = c.get(f"/api/conversations/{conv}/export", base_url=BASE)
ok(r.status_code == 200 and "蒸腾" in r.data.decode(), "导出 Markdown")
s = c.get(f"/api/stats?conversation_id={conv}", base_url=BASE).get_json()
ok(s["current_chat_tokens"] > 0, "Token 统计", str(s["current_chat_tokens"]))

# 14. 权限：非管理员访问后台
r = c.get("/admin", base_url=BASE)
ok(r.status_code == 403, "非管理员禁止访问后台")
# 15. 跨用户访问文件
with app.app_context():
    other = get_db().execute("SELECT id FROM uploaded_files WHERE user_id != (SELECT id FROM users WHERE username='smoke_tester') LIMIT 1").fetchone()
if other:
    ok(c.get(f"/api/files/{other[0]}", base_url=BASE).status_code == 404, "不能访问他人文件")

print(f"\n完成，失败 {failed} 项")
sys.exit(1 if failed else 0)
