#!/usr/bin/env bash
set -euo pipefail
umask 077
archive=${1:?archive required}
expected=${2:?sha256 required}
version=${3:?version required}
[[ "$version" =~ ^deploy-jfcs62-[0-9]{8}T[0-9]{6}Z$ ]]
base=/root/zt_dir/ai_base/DuckKing_ux_review
release="$base/releases/$version"
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c -
mkdir -p "$base/releases" "$base/artifacts/agent" "$base/logs" "$base/state/jobs" "$base/artifacts/projects" "$base/workbench"
test ! -e "$release"
mkdir "$release"
tar -xzf "$archive" -C "$release"
cd "$release"
export PATH="/root/.local/bin:$PATH"
uv venv --python /root/.local/bin/python3.12 .venv
uv export --frozen --all-groups --no-emit-project --format requirements-txt > requirements.lock.txt
uv pip sync --python .venv/bin/python --require-hashes --default-index https://mirrors.aliyun.com/pypi/simple requirements.lock.txt
uv sync --frozen --offline
export PLAYWRIGHT_BROWSERS_PATH="$base/browser-cache"
# Playwright 1.64 omits Ubuntu 20.04 from its download table. The generic Linux
# ffmpeg archive is identical across supported Ubuntu x64 targets; verify it below.
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 .venv/bin/playwright install ffmpeg
.venv/bin/python - "$base" <<'PY'
import pathlib, sys, yaml
base = pathlib.Path(sys.argv[1])
config = yaml.safe_load(pathlib.Path('configs/duckking.yaml').read_text())
config['app']['source_repo'] = '../source/DuckKing'
config['output_dir'] = str(base/'artifacts/agent')
pathlib.Path('configs/jfcs62.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
PY
.venv/bin/ai-ux --config configs/jfcs62.yaml validate-config
.venv/bin/ai-ux --config configs/jfcs62.yaml doctor
mkdir -p verification
.venv/bin/pytest -q --cov=ai_ux --cov-branch --cov-report=json:verification/coverage.json --cov-report=term --junitxml=verification/tests.xml
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context(record_video_dir='verification/browser-video')
    page = context.new_page()
    response = page.goto('https://duck.qbox.net/api/health', wait_until='domcontentloaded')
    assert response.ok
    print({'browser':browser.version,'title':page.title(),'url':page.url})
    page.wait_for_timeout(1000)
    context.close()
    video = page.video.path()
    browser.close()
    import pathlib, subprocess
    assert pathlib.Path(video).stat().st_size > 0
    subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',video],check=True)
PY
ln -s "$release" "$base/.current-$version"
mv -Tf "$base/.current-$version" "$base/current"
printf '%s\n' "INSTALL_OK $version"
