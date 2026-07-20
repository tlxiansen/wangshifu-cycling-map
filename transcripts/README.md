# 加密字幕缓存

此目录只允许提交 `*.json.enc` 加密字幕缓存。完整字幕不会在网页中公开，仓库里
也不会出现可直接阅读的字幕文本。

- 文件名使用视频 BV 号；
- 字幕分段包含开始时间、结束时间和识别文本；
- 内容使用 `TRANSCRIPT_ENCRYPTION_KEY` 经过 PBKDF2-SHA256 派生密钥后，
  再用 Fernet 加密；
- 加密密码只保存在 GitHub Actions Secret 中；
- DeepSeek 分析失败时仍会提交加密缓存，下次运行直接重试文本分析，不再重复
  下载视频或调用语音识别。

不要删除或更换 `TRANSCRIPT_ENCRYPTION_KEY`，否则已有缓存将无法解密。不要向
此目录提交 `.json`、`.txt`、`.vtt` 或 `.srt` 明文字幕。
