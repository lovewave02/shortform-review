# 제작 파이프라인의 최종 export 후 검사

숏폼 제작 스킬이 최종 MP4를 만든 직후(재출력하면 다시) 이 검사를 돌린다. 목적은 매 제작에서 도구가 실제로 동작하는지 확인하고, 사람이 확인할 구간 목록을 QA 보고에 붙이는 것이다. 기존 제작 QA를 대신하거나 막지 않는다.

```sh
python3 <this skill>/scripts/postexport_review.py <final.mp4> \
  [--srt <최종본과 맞는 SRT>] --runs-root <비공개 결과 폴더>/<pipeline>/<run_id> --label <variant>
```

- 실행 파일은 `--bin` → `SHORTFORM_REVIEW_BIN` → 이 체크아웃의 `.venv` → PATH 순서로 찾는다. 전역 설치하지 않는다.
- `--srt`는 최종 export와 같은 자막일 때만 넘긴다. 음성 실험용 SRT나 이전 편집본 자막은 넘기지 않는다. 없으면 자막 검사가 생략된 채로 기록된다.
- `--runs-root`는 공개 저장소 밖이거나 gitignored인 비공개 폴더다. 실행마다 `<label>-<UTC 시각>/`(보고서)와 같은 이름의 `.summary.json`이 새로 생기고 이전 결과를 덮어쓰지 않는다.
- 이 스크립트의 종료코드는 CLI(0/1/2)와 다르다. 후보 유무는 종료코드가 아니라 `state`로 판단한다.
- MP4가 생기면 qualification·패키지 점검 등 다른 게이트의 결과와 상관없이 먼저 실행한다. 이미 만든 run을 이어받았는데 이 검사 기록이 없으면 그때 실행한다.

## 결과 읽기

출력 JSON 한 줄과 `summary_path`의 요약 파일을 읽는다.

| 종료 | status | 의미와 행동 |
|---|---|---|
| 0 | `PASS_REVIEW_COMPLETE` | 검사가 끝까지 실행됨. `state`가 `needs_review`이면 후보 목록을 QA 보고에 붙인다. 후보는 사람이 `report.html`의 `Play ±1 s`로 확인할 목록이며 export 실패가 아니다. `clean`도 게시 승인이 아니다. |
| 2 | `HOLD_REVIEW_INCOMPLETE` | 일부 검사가 실패함. `errors`를 그대로 보고한다. 같은 명령을 반복하지 말고 입력·FFmpeg 상태를 먼저 본다. |
| 2 | `HOLD_REVIEW_NOT_RUN` | 실행 파일이 없거나 보고서가 생기지 않음. `reason`을 보고하고 도구 설치 상태를 확인한다. |

종료 2는 검사 도구의 HOLD다. 기존 제작 QA·업로드 게이트의 판정을 바꾸지 않으며, 사용자에게 알리지 않고 넘어가지도 않는다. 보고에는 `status`, `state`, `candidates_by_rule`, 자막 검사 실행 여부, `report_html` 경로를 적는다.
