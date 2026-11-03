# 来源与整合取舍

本项目为独立实现，借鉴下列公开项目的设计方向；不捆绑其代码、提示词全文或小说语料，不要求安装这些项目，也没有获得这些项目的背书。

| 参考项目 | 借鉴的方向 | 本项目的实现 |
|---|---|---|
| [chinese-novelist-skill](https://github.com/PenglongHuang/chinese-novelist-skill) | 清晰的立项到交稿流程、人物档案与摘要 | 按任务规模选择轻流程或账本流程，修正非正文计数和失败仍完成问题 |
| [Chinese-WebNovel-Skill](https://github.com/Tomsawyerhu/Chinese-WebNovel-Skill) | 专项问题路由、正反例辅助修改 | 原创的症状导向写作模块与微型示例，不附原项目范文库 |
| [webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) | 写前取上下文、写后记录事实、审稿与恢复 | SQLite权威记录、串行提交、明确的草稿/已验收边界 |
| [MyNovel](https://github.com/xiaofeng-928/chinese-longnovel-skill) | 事实证据、正文版本与审稿绑定、历史修改留痕 | 正文/来源/提案哈希校验、事务写入、保留原项目的历史分支 |
| [MuMuAINovel](https://github.com/xiamuceer-j/MuMuAINovel) | 可视化人物、章节与伏笔管理 | 无部署依赖的离线只读面板；没有移植完整编辑器与账号系统 |

2026-11-03 查看时，首项目及MyNovel有MIT许可，webnovel-writer与MuMuAINovel为GPL-3.0；Chinese-WebNovel-Skill的v2根目录未找到LICENSE。以上为当时文件观察，不是对所有分支或素材许可的法律结论。因此首版采用独立代码与原创说明，避免直接拼接不同项目的实现和来源不明的小说语料。

## 首版边界

- 接入宿主已有的模型和工具，不自带模型、不训练文风、不承诺同模型下成文效果必然更好。
- 检索为精确关键词检索，未接入向量数据库或Embedding服务；大规模故事仍需人工判断召回范围。
- 脚本验证结构、引用存在、版本关系和状态回放，不能证明文学质量、证据语义、审稿诚实性或角色心理合理。
- 两种审稿维度不等于两名独立审稿者；执行方式必须披露。
- SQLite提交原子化；文件导出不是跨数据库/文件系统的同一事务。导出中断通过missing_export检测并重建。
- 单个作品串行推进，支持数据库锁与head检查；不提供跨设备同步与多人实时编辑。
- 面板是静态只读快照，首版不含可视化改稿、自动语义历史迁移、EPUB与平台发布。
