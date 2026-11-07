# Report design

The report helps a video producer inspect candidate intervals in chronological order. The primary task is understanding coverage, then locating a candidate in the matching local export.

Show status and executed/skipped/failed checks prominently before findings. A zero-finding report must retain coverage and limitations. Distinguish analysis failures from candidate issues and intentional editorial choices.

Use a readable, responsive single-page HTML report with a local-file video picker, seek and context-playback controls and a semantic findings table. Context playback plays one second before and after a candidate and stops automatically, because a short pause or black interval is judged by what surrounds it; reviewer actions (pause, Seek, timeline jumps outside the window) cancel the automatic stop. Work offline without external assets or requests. Escape all source-derived content. Preserve keyboard access, legible contrast and visible labels; on narrow screens allow table scrolling without clipping the page.

Do not embed the video or absolute source paths. A locally selected video is not verified solely by its filename; input hashes in the report identify the analyzed artifact. Human triage remains separate from the generated report.
