"""依赖升级后的最小冒烟：应用能导入并启动、登录页和 /healthz 能打开、pypdf / openpyxl 能从文件里取出文字。

用法（在仓库根目录）：python scripts/smoke_deps.py
镜像里：docker run --rm -i --entrypoint python <镜像> - < scripts/smoke_deps.py
"""

import os
import secrets
import sys
import tempfile
from importlib.metadata import version

sys.path.insert(0, os.getcwd())


def build_pdf(text: str) -> bytes:
    """手工拼一个只有一页、一行文字的 PDF，不依赖任何第三方库。"""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


def main() -> int:
    for name in ("flask", "gunicorn", "pypdf", "requests", "python-dotenv", "python-docx", "google-genai", "openpyxl", "python-pptx"):
        print(f"{name} {version(name)}")

    workdir = tempfile.mkdtemp(prefix="ai_chat_smoke_")
    os.environ.update(
        {
            "AI_CHAT_SITE_SECRET_KEY": secrets.token_hex(32),
            "AI_CHAT_SITE_ALLOWED_HOSTS": "localhost",
            "DATABASE_PATH": os.path.join(workdir, "smoke.sqlite3"),
            "UPLOAD_DIR": os.path.join(workdir, "uploads"),
            "FORCE_HTTPS": "0",
        }
    )

    from ai_chat_site import create_app
    from ai_chat_site.files_service import extract_text

    app = create_app()
    login_url = None
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "auth.login":
            login_url = rule.rule
    if not login_url:
        print("失败：没找到登录页路由 auth.login")
        return 1
    resp = app.test_client().get(login_url, base_url="http://localhost")
    print(f"登录页 {login_url} 返回 {resp.status_code}")
    if resp.status_code != 200:
        print("失败：登录页没有返回 200")
        return 1

    health = app.test_client().get("/healthz", base_url="http://localhost")
    print(f"/healthz 返回 {health.status_code} {health.get_data(as_text=True).strip()}")
    if health.status_code != 200:
        print("失败：/healthz 没有返回 200")
        return 1

    # extract_text 出错时要写日志（current_app.logger），所以放在应用上下文里跑
    ctx = app.app_context()
    ctx.push()

    pdf = build_pdf("Hello smoke 20261127")
    text = extract_text(pdf, "smoke.pdf", "application/pdf")
    print(f"PDF 提取结果：{text!r}")
    if "Hello smoke 20261127" not in text:
        print("失败：没有从 PDF 里取出预期文字")
        return 1

    # 损坏的 PDF 应该被吞掉返回空串，不能把异常抛给上传接口
    broken = extract_text(b"%PDF-1.4\nnot a pdf", "broken.pdf", "application/pdf")
    print(f"损坏 PDF 提取结果：{broken!r}")
    if broken != "":
        print("失败：损坏的 PDF 没有返回空串")
        return 1

    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["smoke", 20261128])
    buf = io.BytesIO()
    wb.save(buf)
    sheet = extract_text(buf.getvalue(), "smoke.xlsx", None)
    print(f"xlsx 提取结果：{sheet!r}")
    if "smoke,20261128" not in sheet:
        print("失败：没有从 xlsx 里取出预期内容")
        return 1

    print("冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
