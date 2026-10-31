# 报告v2严格Schema传输

`runtime_strict_schema.py` 是 Codex 和 WorkBuddy 自由查询／验收 Harness 共用的传输编解码器，不替代报告业务模型、证据校验或任务准入。

## 业务对象与SDK表示分离

- 平台继续用原报告v1/v2对象和检查器处理 `value_bindings`、完整 `source_key`、金额引用、四项验证与范围收据。
- 原生终态Schema中的动态字典用唯一 `{key,value}` 条目列表表示。类型明确的值仍保留原类型；无约束JSON叶值使用明确声明的精确JSON字符串传输，接收时还原。
- 这不是从普通模型文本提取或拼接JSON。整个终态、列表条目、JSON叶值与还原后的业务对象都必须合法；重复键、未知字段、非有限数及非法JSON拒绝，不猜值、不补造业务字段。
- 成功的报告工具保留 `validated_report`，兼容增加 `native_validated_report`。模型在原生完整终态的 `presentation` 中原样复制后者，包含全部证据绑定与证明；整份presentation不能再序列化成字符串。
- 工具的报告入参仍使用原业务Schema，SDK传输表示不进入业务存储或历史报告。原生终态还原后继续经过原报告引用、金额、来源、范围与平台证明检查。

## 派发前检查和错误

共享Harness在开启业务工具会话／准备SDK执行前编译终态传输Schema；两个驱动在连接SDK之前再次核对严格对象结构。对象必须明确声明 `properties`、关闭额外字段并列出全部必需属性；数组必须声明元素Schema。定义错误使用 `runtime_output_contract_invalid` 和安全字段路径，不派发SDK。

Codex上游仍可能拒绝平台未知的格式约束；明确的 `invalid_json_schema` 分类为 `runtime_native_schema_rejected`，保留原生原因，不误报为SAP零结果。页面对历史同类错误只做只读展示投影，不改写错误记录。

Codex验证后提前收尾保持原路径；WorkBuddy仍要求原生 `StructuredOutput` 和确认清理，候选／普通文本不能代替终态。调查、编辑、测试能力、权限和任务预算没有修改。报告Schema仅缓存代码定义的纯结构，调用方得到独立副本；不缓存认证、证据或准入结论。

## 历史及上线

原v1输出Schema保持一致；原已部署报告v2的精确检查器绑定兼容读取，未知或篡改绑定继续拒绝。旧记录不改写，不从新规则追授PASS。

2026-10-30完成离线验证及受控上线：404项冻结后端、60项前端及36组前台模拟通过，目录、Astro和隔离生产构建通过。前台模拟使用原问题，但API、SDK与证据均为合成夹具，真实模型／SAP调用与真实查询重跑为零。

实际源码 `2ccbd3f25f57d240fcd30f17c5d7a10418ee5ca7ca9d513144be0126e9ef3070`；前台 `8ccc34e21f78738632cc3ed4fc84993025cb4a84d18ecc05c2f35c8400368782`。原SDK真实通信证据结合本次目标测试追加基础设施凭据，不改写业务PASS或旧模型检查。详情与回退依据在 `.local-data/report-strict-schema-20261030/`。

离线与模拟通过不代表已完成本次真实模型／SAP业务验证；原失败查询仍保留，不自动重试。没有升级SDK、CLI或worker，没有提交或推送。
