# Procure-to-Pay Status Assistant

一个可直接运行的 SAP MM/FI-AP 纵向切片：从自然语言问题中提取采购订单和可选行项目，沿 `PO → GR → IV → FI → Payment` 凭证链逐项目判断状态，并给出阻塞原因与原始凭证证据。

## 已实现能力

- 中英文自然语言入口：识别 6–12 位显式 PO，或独立的 10 位采购订单号；支持 `项目 20` / `item 20`。
- 跨对象关联：`EKKO/EKPO`、`EKBE`、`MKPF/MSEG`、`RBKP/RSEG`、`BKPF/BSEG`。
- 项目级状态：未收货、部分收货、已收货未发票、部分发票、已发票未付款、部分付款、已付款、已删除/取消。
- 净额处理：按借贷标识对收货/冲销、发票/贷项做正负汇总；MSEG 缺失时明确标注并回退到 EKBE。
- 异常解释：短交关闭、超收、发票数量超过收货、无 GR 发票、未过账发票、发票冻结、付款冻结、未到期/逾期、FI 关联缺失、非付款凭证清账。
- 付款识别：通过发票 FI 供应商行的 `AUGBL/AUGGJ` 追踪清账凭证并校验付款凭证类型；也支持通过 `REBZG/REBZJ` 识别部分付款。
- 人类可读 Markdown 与稳定 JSON 两种输出。
- 无真实 SAP 时使用包内 SAP-like JSON fixture；生产适配器只需实现一个协议。
- 可读取由 Embedded Provider 或受控 SAPSkillhub Skill 生成的脱敏 evidence 快照；数据采集与业务分析保持解耦。

## 快速运行

要求 Python 3.11+。所有命令都在本目录执行：

```powershell
cd D:\SAPBusinessAgents\agents\MM\procure-to-pay-status
python -m pip install -e .
p2p-status "PO 4500001234 是否已经收货、发票校验和付款？" --as-of 2026-07-22
```

只看一个项目：

```powershell
p2p-status "采购订单 4500001234 项目 40 的付款状态"
```

机器可读输出：

```powershell
p2p-status "PO 4500001234 item 50" --json
```

读取真实 SAP 验证编排层生成的 evidence：

```powershell
p2p-status "PO 4500001234 是否已经收货、发票校验和付款？" `
  --source evidence `
  --evidence D:\SAPBusinessAgents\.local\runs\procure-to-pay-status\RUN_ID\sap-read-evidence.json `
  --payment-document-types KZ,ZP,PY `
  --as-of 2026-08-09 `
  --json
```

evidence 必须明确标记 `completeness.complete=true`，并包含 PO、物料凭证、供应商发票和 FI/清账实体。缺页、币种不一致或单位不一致时数据源会拒绝分析，而不是生成推测性状态。

不安装也可运行：

```powershell
$env:PYTHONPATH = "src"
python -m procure_to_pay_status "PO 4500001234 是否已付款？" --as-of 2026-07-22
```

演示 PO `4500001234` 有 6 个项目，分别覆盖未收货、部分收货、已收货未发票、冻结且逾期的已发票未付款、已付款、部分付款。

## 结构

```text
procure-to-pay-status/
├─ src/procure_to_pay_status/
│  ├─ extractor.py       # 确定性 PO/item 参数抽取
│  ├─ port.py            # 可替换 SAP 数据源协议
│  ├─ fixture.py         # JSON fixture 适配器及远端过滤模拟
│  ├─ analyzer.py        # 跨表关联、金额分摊、状态机、异常解释
│  ├─ assistant.py       # 自然语言应用服务入口
│  ├─ formatting.py      # 逐行 Markdown 输出
│  ├─ cli.py             # CLI / JSON 输出
│  └─ fixtures/          # 不含凭据的演示数据
├─ tests/                # 参数、状态边界、完整链路与 CLI 测试
└─ docs/                 # SAP 接口契约和判定规则
```

核心边界是 `P2PDataSource.load_purchase_order(po_number) -> P2PTables`。真实 RFC、OData、CDS 或数据仓库适配器负责高效、授权地读取相关记录；`P2PAnalyzer` 始终负责关联和业务判断，因此 fixture 与生产环境复用同一套状态逻辑。

## 状态优先级

状态代表当前最前面的未完成业务阶段。若前序阶段未完成，即使已经发生后序凭证，仍保留前序状态并追加异常。例如“收货 4/10、发票 10/10”仍显示“部分收货”，同时报告“发票数量超过收货”。

1. 删除标识 → 已删除/取消
2. 净收货 `<= 0` → 未收货
3. 净收货 `< 订单数量` → 部分收货
4. 已过账净发票 `<= 0` → 已收货未发票
5. 净发票数量 `< 净收货` → 部分发票
6. 已确认付款金额覆盖发票 → 已付款
7. 已确认部分付款 → 部分付款
8. 其余 → 已发票未付款

数量比较使用 `0.0001` 容差。付款金额按发票级已付比例分摊到 PO item，避免一个多项目发票把整笔付款重复计到每个项目。

## 测试

测试只依赖 Python 标准库：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 生产接入注意事项

- fixture 不含真实凭据或个人数据；认证、重试、分页、权限与连接池属于适配器职责。
- `KZ/ZP/PY` 是当前已知付款凭证类型，客户自定义凭证类型必须配置或映射后才能判定为付款；仅有 `AUGBL` 不等于付款。
- `FAEDT` 在接口契约中是可选的派生净到期日。若源端只提供 `ZFBDT/ZTERM/ZBD*T`，应在适配器或 SAP 标准函数中计算，不能把基准日直接当到期日。
- `--payment-document-types` 默认使用 `KZ,ZP/PY`；客户自定义付款凭证类型必须显式配置。
- S/4HANA 可从 CDS/API 或 MATDOC 兼容视图提供与本契约等价的字段，不要求直接读取透明表。
- 当前参数抽取故意保持确定性与可审计性。需要自由表达时，可在上层增加 LLM extractor，但应返回相同 `QueryParameters` 并保留此实现作为校验/回退。

详细字段与查询顺序见 [SAP 数据契约](docs/sap-data-contract.md)。
