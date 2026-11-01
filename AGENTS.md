# Project workflow

Build a small local-first video and subtitle review tool. Python runtime uses the standard library; FFmpeg/ffprobe are external programs. Keep parsing, media inspection, review rules, report rendering and CLI separate. Preserve existing public interfaces unless a versioned change is intentional.

Run `python -m unittest discover -s tests -v` after relevant changes. Include real FFmpeg fixture tests when installed. Detection findings identify review candidates, not automatic editorial errors. Missing dependencies or failed analysis must never appear as a clean review.

Never commit real production media, user subtitle files, generated reports, absolute personal paths or credentials. Fixtures must be synthetic. Review untracked files and the staged diff before publication.
