"""Publish the UI to the installed dashboard without requiring a server restart.

Run with the project Python after frontend edits. The repository keeps separate
CSS/JS sources; the installed HTML bundles them for compatibility with older
servers' asset allowlists. Runtime state and credentials are never copied.
"""
from pathlib import Path
import base64
import os
import tempfile

ROOT = Path(__file__).resolve().parent
DESTINATION = Path('/Users/fbelsabah/Documents/Codex/2026-09-27/can/outputs/dashboard')


def deploy():
    html = (ROOT/'index.html').read_text()
    mark = 'data:image/png;base64,' + base64.b64encode((ROOT/'brand/coursewell-mark.png').read_bytes()).decode()
    html = html.replace('/brand/coursewell-mark.png', mark)
    css = (ROOT/'ui.css').read_text().replace('@import url("/study_ai.css");', (ROOT/'study_ai.css').read_text())
    art = 'data:image/png;base64,' + base64.b64encode((ROOT/'brand/coursewell-watercolors.png').read_bytes()).decode()
    css = css.replace('/brand/coursewell-watercolors.png', art)
    js = (ROOT/'ui.js').read_text()
    html = html.replace('<link rel="stylesheet" href="/ui.css">', '<style>'+css+'</style>')
    html = html.replace('<script src="/ui.js"></script>', '<script>'+js+'</script>')
    # Calendar styling now lives in the bundled design system.
    html = html.replace('<script src="/calendar.js"></script>', '<script>'+(ROOT/'calendar.js').read_text()+'</script>')
    html = html.replace('<script src="/study_ai.js"></script>', '<script>'+(ROOT/'study_ai.js').read_text()+'</script>')
    html = html.replace('<script src="/discover.js"></script>', '<script>'+(ROOT/'discover.js').read_text()+'</script>')
    html = html.replace('<script src="/settings.js"></script>', '<script>'+(ROOT/'settings.js').read_text()+'</script>')
    fd, temporary = tempfile.mkstemp(prefix='.frontend-', dir=DESTINATION)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(html)
        os.replace(temporary, DESTINATION/'index.html')
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Published dashboard UI; runtime data unchanged.')


if __name__ == '__main__':
    deploy()
