# AR Collection Assistant

这是一个可独立运行的 FI-AR 纵向切片：自然语言问题进入后，读取客户、未清项、付款历史和未认领银行到账，完成账龄计算、到账匹配建议、风险评分、催收优先级与可审阅沟通草稿，最后返回稳定的 JSON 结构。

本实现默认使用 fixture，不依赖真实 SAP，也不会产生外部副作用：

- SAP 数据边界只读；不自动清账、不记账。
- 到账匹配是建议，不是 FEBAN/会计凭证更新。
- 催款内容只生成草稿；`communication_auto_send=false`，每份草稿均要求人工审核。
- 催款冻结或全部处于争议的客户返回 `HOLD_REVIEW`，不生成对客正文。

## 快速运行

要求 Python 3.11 或更高版本，无第三方运行时依赖。

```powershell
cd D:\SAPBusinessAgents\agents\FI\ar-collection
$env:PYTHONPATH = "src"
python -m ar_collection_assistant "列出本周需要催收的客户" --as-of 2026-07-22
```

其他已支持问题：

```powershell
python -m ar_collection_assistant "查看 C1000 的未清项目与账龄" --as-of 2026-07-22
python -m ar_collection_assistant "列出未匹配银行到账" --as-of 2026-07-22
```

也可以安装为独立命令：

```powershell
python -m pip install -e .
ar-collection "列出本周需要催收的客户" --as-of 2026-07-22
```

通过 `--fixture D:\path\custom.json` 可替换演示数据。Fixture 的字段结构参见 `src/ar_collection_assistant/fixtures/demo_ar.json`。

## 从代码调用

```python
from datetime import date
from ar_collection_assistant import ARCollectionAssistant, FixtureARGateway
from ar_collection_assistant.models import to_primitive

assistant = ARCollectionAssistant(FixtureARGateway())
result = assistant.query("列出本周需要催收的客户", date(2026, 7, 22))
json_ready_result = to_primitive(result)
```

生产环境只需实现 `ARDataGateway.load_snapshot(as_of)` 并注入 `ARCollectionAssistant`。领域逻辑不依赖 SAP SDK、RFC 或具体 OData 客户端。

## 处理链路

1. `intent.py` 识别催收周清单、客户账龄、未匹配到账三类意图，并从问题中解析客户编号/名称。
2. `ports.py` 定义只读 `ARDataGateway`；`fixture_gateway.py` 是可替换的本地实现。
3. `aging.py` 按 `current / 1-30 / 31-60 / 61-90 / >90` 自然日分桶。
4. `matching.py` 对银行到账与发票做可解释匹配，输出 `exact / likely / unmatched`，不执行清账。
5. `scoring.py` 计算 0-100 风险分、P1-P4/HOLD 优先级和本周行动日期。
6. `drafting.py` 生成中文催款草稿；有冻结/全部争议时仅创建内部复核任务。
7. `service.py` 编排全链路并返回版本化 JSON 输出。

## 匹配与评分规则

到账候选匹配分由以下证据组成：发票/凭证/参考号精确匹配 0.55（部分匹配 0.35）、金额精确匹配 0.30（2% 内 0.15）、付款人名称 0.10、到账日期合理 0.05。候选分低于 0.65 或前两名差值小于 0.10 时保持 `unmatched`；参考号和金额均精确时标记 `exact`。一个未清凭证在同批建议中最多被一个到账占用。

风险分是可审计的加权规则：

| 因子 | 最大影响 |
|---|---:|
| 净逾期金额 / 信用额度 | +25 |
| 最大逾期天数 | +30 |
| 平均延迟、历史准时率、失约次数 | +20 |
| 当前信用额度使用率 | +15 |
| 待复核到账覆盖比例 | -15 |

`risk.breakdown` 返回每一项实际得分。优先级规则为：超过 90 天、信用使用率超过 120% 或分数至少 65 为 P1；分数至少 45 或超过 60 天为 P2；分数至少 25 为 P3，其余为 P4。催款冻结或全部逾期款有争议时覆盖为 `HOLD_REVIEW`。待复核到账先从建议催收金额中扣减，防止对候选已付款发票直接催收。

规则是演示基线；生产上线前应由财务、信用控制、法务和模型风险管理共同确认阈值，并保留版本与审批记录。

## 输出契约

顶层结构保持稳定并带 `schema_version`：

- `summary`：客户数、按币种的待处理总额、可直接跟进总额、高风险客户数、未匹配到账数。不同币种不互相汇总。
- `customers[]`：客户主数据、账龄和逐笔未清项、风险及分项、行动优先级、匹配建议、沟通草稿。
- `unmatched_bank_receipts[]`：无法可靠匹配的银行到账及 `manual_research_in_feban` 建议。
- `controls`：只读、未清账、未发送、人工审核四项控制证据。
- `data_lineage`：源系统、抽取时间、SAP 模块范围。

所有金额在 JSON 中序列化为十进制字符串，避免二进制浮点误差。请求 ID 由查询文本和 `as_of` 稳定生成，便于幂等追踪。

## SAP 接口映射

生产适配器应优先使用获批的 released API/CDS/RFC，而不是从业务代码直接查询表。下面是接口字段的典型来源与人工核对入口：

| 领域数据 | SAP 范围 | 典型对象 / T-code |
|---|---|---|
| 客户未清项、到期日、金额 | FI-AR | BSID，BKPF/BSEG；FBL5N、FB03 |
| 已清项与付款习惯 | FI-AR | BSAD，BKPF/BSEG；FBL5N、FD10N |
| 客户公司代码属性、催款冻结 | FI-AR 主数据 | KNA1、KNB1；FD10N、F.27 |
| 开票参考与发票明细 | SD-Billing | VBRK、VBRP；VF03 |
| 未认领银行到账 | Bank Accounting | 银行对账单/电子银行对账单接口；FEBAN |
| 信用额度与当前敞口 | Credit Management | S/4HANA Credit Management released API/CDS（或经批准的传统信用主数据接口） |

`KNB1` 本身不提供完整信用额度，因此生产适配器不能用它臆造该字段。数据不足时应返回“不可评分/需复核”，而不是默认为零。

建议以同一抽取时间形成快照，并传入：

- `accounts`：客户、公司代码、币种、信用额度、当前敞口、催款冻结。
- `open_items`：会计凭证、开票凭证、过账/到期日、未清金额、争议状态。
- `payment_history`：平均晚付天数、准时付款率、12 个月失约次数。
- `unmatched_receipts`：到账 ID、起息日、金额、币种、付款人、附言和银行账户。

## 测试

```powershell
cd D:\SAPBusinessAgents\agents\FI\ar-collection
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

测试覆盖账龄边界、精确/未匹配到账、周催收清单、到账扣减、P1 与 HOLD、草稿安全控制、当前未清项查询和 JSON 金额精度。

## 目录

```text
agents/FI/ar-collection/
├── pyproject.toml
├── README.md
├── src/ar_collection_assistant/
│   ├── aging.py
│   ├── matching.py
│   ├── scoring.py
│   ├── drafting.py
│   ├── intent.py
│   ├── ports.py
│   ├── fixture_gateway.py
│   ├── service.py
│   ├── cli.py
│   └── fixtures/demo_ar.json
└── tests/
```
