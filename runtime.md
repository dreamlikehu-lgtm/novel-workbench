# 命令与数据契约

Python 3.10+，无第三方依赖。由助手运行，不要求用户手工维护 JSON。以下 `$SKILL` 表示技能安装根目录，`$BOOK` 表示用户指定的小说目录；运行时用真实绝对路径或正确引用的 shell 变量替换。示例文件名需要先由助手写好。

## 创建与配置

```sh
python3 "$SKILL/scripts/novel.py" init "$BOOK" --title "回声之前" --genre "悬疑" --planned-chapters 12 --minimum 2000 --maximum 3500 --metric han
```

目录必须不存在，避免覆盖旧项目。minimum 为正整数；maximum 可省略以关闭上限；planned-chapters 可省略用于开放连载。metric=han 统计汉字，visible 统计所有非空白字符（含标点、英文、数字）。二者不等于平台统计规则。

目录内容：

```text
作品目录/
  bible.md                 人物与世界设定，可编辑
  outline.md               未来规划，可编辑
  style.md                 本书文风约定，可编辑
  drafts/                  助手编写候选正文、摘要与增量文件
  chapters/0001.md          已验收章节的导出视图
  reports/                 审稿报告、合集和只读面板
  .novel/ledger.sqlite3     权威正文、事实快照、审稿版本与提交链
```

不要手工改数据库。全文备份须包含隐藏的 `.novel` 目录；仅复制 chapters 不包含事实与审稿记录。首版配置在 init 时确定，改规格请新建分支项目并安排迁移，不能直接篡改数据库绕过验收。

## 上下文与候选

```sh
python3 "$SKILL/scripts/novel.py" context "$BOOK" --focus "林舟,钥匙"
```

返回 `head` 与 `source_sha`，以及当前事实、到期伏笔、近三章原文、近20章摘要和最多5条关键词匹配片段。focus 是精确关键词匹配，不是语义检索。被省略的事实键会列出；不能据此认定其他事实无关。查询较大项目时按需回查。

候选章节默认使用以下分区，标题、概要和备注不计入正文：

```markdown
# 第1章 雨停之前

## 本章规划
只供创作参考。

## 正文
实际故事正文放在这里。

## 编写备注
只供审稿参考，不计正文。
```

`## 正文` 必须且只能出现一次；到下一个一级或二级标题结束。故事正文不要使用一级或二级标题作场景分隔。纯正文文件可显式加 `--format body`，此时全文件计数。

另外准备摘要文本 `summary.txt` 与 `delta.json`。摘要记录关键后果及未了状态，不限死字段数量。事实候选示例：

```json
[
  {"key":"character:林舟:钥匙归属","value":"林舟持有","evidence":"林舟把钥匙放进口袋"},
  {"key":"thread:蓝信封","value":{"status":"open","due_chapter":4,"question":"谁送来了信封？"},"evidence":"门缝里塞着一个蓝信封"}
]
```

每条 evidence 必须为本章正文中至少4字符的精确片段。key 不得重复；普通 value 可为 JSON 值。伏笔 key 以 thread: 开头，value.status 为 open/resolved/dropped，due_chapter 可为正整数或 null。无变化使用空数组。删除或失效的事实用明确值表达，保留历史来源，不静默抹除。

```sh
python3 "$SKILL/scripts/novel.py" stage "$BOOK" --chapter 1 --title "雨停之前" --file "$BOOK/drafts/01.md" --summary "$BOOK/drafts/summary.txt" --delta "$BOOK/drafts/delta.json" --expected-head genesis --expected-source "context返回的source_sha"
```

stage 的 expected-head 和 expected-source 必须来自写作前的 context；写作期间上文或来源变动时先复核。stage 返回正文 SHA、来源 SHA、提案 SHA（标题、摘要和增量）、base_head 与字数。length_pass=false 允许保存草稿，但不能提交。只能 stage 下一章，不能覆盖已验收章节。

## 审稿

审稿者先运行以下命令，读取暂存的实际版本，不能只看可能已经修改的外部草稿文件：

```sh
python3 "$SKILL/scripts/novel.py" candidate "$BOOK" --chapter 1
```

审稿者必须阅读此版本的正文、摘要、事实候选与相关设定，再编写报告。下面的 SHA 值取自 candidate 或 stage 的实际输出，不自行拼造：

```json
{
  "reviewer":"实际执行者或分轮复查标识",
  "scope":"continuity",
  "body_sha":"stage返回的正文哈希",
  "base_head":"stage返回的base_head",
  "source_sha":"stage返回的来源哈希",
  "proposal_sha":"stage返回的提案哈希",
  "verdict":"pass",
  "findings":[],
  "notes":"说明实际检查的时间线、人物认知、证据对事实的支持及结论。"
}
```

另一份报告 scope=craft。verdict=pass/fail；findings 条目需要 severity=blocking/advisory 和具体 message。有 blocking 时不得通过。报告 reviewer 字段仅作追踪，脚本不能证明其身份或独立性；不得伪造多人审稿。

```sh
python3 "$SKILL/scripts/novel.py" review "$BOOK" --chapter 1 --file "$BOOK/reports/01-continuity.json"
python3 "$SKILL/scripts/novel.py" review "$BOOK" --chapter 1 --file "$BOOK/reports/01-craft.json"
python3 "$SKILL/scripts/novel.py" commit "$BOOK" --chapter 1 --expected-head genesis
```

有当前版本失败报告时不能提交；相同 reviewer/scope 的复查报告会更新原记录。其他审稿者的失败结论也须由相应复查或新候选版本解决，不能忽略。更改候选后重新 stage，旧版本报告不会被当前提交采用。

数据库提交成功后才导出 Markdown；若进程恰在两者之间中断，status 会列出 missing_export，运行 export 恢复。返回 export_pending 时已提交数据库，不能重复提交。

## 查询、导出与历史修订

```sh
python3 "$SKILL/scripts/novel.py" status "$BOOK"
python3 "$SKILL/scripts/novel.py" dashboard "$BOOK"
python3 "$SKILL/scripts/novel.py" export "$BOOK"
python3 "$SKILL/scripts/novel.py" fork "$BOOK" --from-chapter 3 --to "新的作品分支目录"
```

status 检查提交链、状态回放与导出文件。只有计划章节全部提交且无待处理草稿或视图问题，才返回 completed。开放连载保持 in_progress，不代表本次指定的章节任务没有完成。

dashboard 是离线只读快照；export 汇总已验收正文。手改章节会阻断续写和普通导出。要保留改稿，先另存并 fork；明确需要恢复旧稿时使用 `export --restore`，脚本会为被覆盖文件生成备份。

fork 从指定章开始重写，仅保留此前提交。它不是自动语义迁移；重新规划和重提后续章节。原项目不会修改。

所有命令成功返回 JSON，退出码0；参数、校验或读写失败退出码2。Python 异常、模型不可用、未通过验收分别说明，不能只凭进程退出或文件存在宣布作品合格。
