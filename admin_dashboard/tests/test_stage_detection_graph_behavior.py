"""신규 그래프의 이벤트 처리를 기존 화면과 분리해 검사한다."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_scoped_graph_selection_and_common_detail():
    node=shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for the isolated JavaScript behavior check')
    directory=Path(__file__).parent
    javascript=directory.parent/'src/ticket_guard_admin/static/stage_detections.js'
    result=subprocess.run([node,str(directory/'check_stage_detection_graph.cjs'),str(javascript)],
                          capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
