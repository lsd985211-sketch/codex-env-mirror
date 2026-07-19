# DOCX + Mermaid 报告生成

## 目标

把 Markdown 总览类内容稳定转成适合 Word 阅读的 `.docx`，尤其是含 Mermaid 图、表格和长说明的报告。

## 默认流程

1. 调用 `load_workspace_dependencies`，记录 bundled Python、Node 和文档库路径。
2. 从唯一 Markdown 源提取 Mermaid；让 Mermaid CLI 从同一 `.mmd` 直接输出 SVG 和 PNG。HTML/PDF 复用 SVG，Word 使用直接渲染的 PNG。
3. 不把 SVG 经 ImageMagick 转 PNG 作为默认路线；该转换只可作为 fallback，并且必须通过图片尺寸、非空内容和 Word readback 检查。
4. 用 bundled Python 和 `python-docx`/Pandoc 生成 DOCX。
5. 对每张图设宽高上限，避免单图撑爆页面；超长图优先调整 Mermaid 布局。
6. 用 Python 结构回读，再用 `cli-anything-microsoft-office word inspect` 做原生 Word 检查。
7. 需要正式 PDF 时，用 Word 原生导出，并用 PDF 解析器复查页数和文本。

## 版式规则

- 纸张默认 A4 或 Letter 均可，但图片必须按可用页宽约束。
- 单图建议宽度不超过 `6.2in`。
- 高图优先改 Mermaid 结构为横向，而不是让 Word 强行缩得太窄。
- 每个大章节前后留足空行，必要时分页。
- 表格列宽要固定，不要依赖自动布局。

## 验证清单

- 图片是否都嵌入成功。
- Mermaid block 数是否等于渲染资产数和 DOCX inline shape 数。
- 是否存在超宽图片。
- 是否存在异常扁平或异常细长图片；只看“文件存在”会漏掉转换失败。
- 表格数量是否符合预期。
- 主标题、章节标题、图注是否都在文档里。
- 输出文件能否被重新打开并读取结构。
- Word 报告的真实页数、段落数、表格数是否可读。
- Word 导出 PDF 与浏览器 PDF 的页数和可提取文本是否合理。
- 发布 HTML 是否使用相对资产路径，而不是工作区绝对路径。

## 推荐工具链

- Mermaid 渲染：`_bridge/render_mermaid_diagrams.js`
- 文档生成：`pandoc`
- 后处理：`python-docx`
- 图像尺寸：`Pillow`
- SVG/PNG 转换：`ImageMagick`，转换后必须检查尺寸和非空内容。
- 原生检查/导出：`cli-anything-microsoft-office word inspect|export-pdf`

## 常见故障与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: docx` | 调用了系统 Python，而不是 bundled runtime | 先调用 `load_workspace_dependencies`，改用返回的 Python |
| SVG 转 PNG 出现 CSS parsing warning | Mermaid SVG 含转换器不完全支持的 CSS | 丢弃该转换产物，让 Mermaid CLI 直接输出 PNG；浏览器截图仅作末级 fallback |
| DOCX 能打开但图过扁/过高 | 仅按宽度缩放或源图画布异常 | 同时设置最大宽高，并检查每张图的宽高比 |
| HTML 在工作区可见、发布后丢图 | 使用了绝对 asset path | 发布时改成相对目录并复制资产 |
| COM 返回成功但最终 PDF 不可信 | 只验证了命令返回码 | 用 PDF parser 回读页数、文本和文件大小 |

## 什么时候使用

- 需要把架构说明、流程图、模块表做成 Word 报告。
- 需要持续重生成同类文档，而不是只修一次成品。
- 需要把“图太高、表太散、版面混乱”变成可重复处理的流程。
