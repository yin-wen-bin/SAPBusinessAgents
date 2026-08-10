# SAP 数据契约与关联规则

## 接口边界

生产数据源实现：

```python
class P2PDataSource(Protocol):
    def load_purchase_order(self, po_number: str) -> P2PTables: ...
```

返回字段沿用大写 SAP 字段名。适配器应尽量在 SAP 侧过滤，不应全表下载。读取方式可以是 RFC/BAPI、OData、released CDS API、数据产品或受控 SQL；分析器不依赖传输方式。

## 建议查询顺序

1. 用 `EKKO-EBELN` 读取抬头；用 `EKPO-EBELN` 读取项目。
2. 用 `EBELN/EBELP` 读取 `MSEG`（或 S/4HANA 等价来源），再按 `MBLNR/MJAHR` 取 `MKPF`。`EKBE` 同时读取作采购订单历史与 MSEG 不可用时的回退。
3. 用 `EBELN/EBELP` 读取 `RSEG`，再按 `BELNR/GJAHR` 取 `RBKP`。
4. 对每个 LIV 发票，以 `BKPF-AWTYP = 'RMRP'` 且 `BKPF-AWKEY` 以前缀 `RBKP-BELNR + RBKP-GJAHR` 关联 FI 凭证。
5. 按 `BUKRS/BELNR/GJAHR` 读取发票 FI 的供应商 `BSEG` 行。
6. 若供应商行有 `AUGBL`，按 `BUKRS/AUGBL/AUGGJ` 读取清账 `BKPF/BSEG`，并以凭证类型确认它是否为付款。
7. 对尚未清账的供应商行，再查 `BSEG-REBZG/REBZJ/REBZZ` 指向原发票 FI 行的付款项，以识别部分付款。

大数据量实现可批量化 3–7 步，但返回给分析器的行必须限定在当前 PO 的凭证闭包内。

## 最小字段

| 表/对象 | 必需或建议字段 | 用途 |
|---|---|---|
| EKKO | `EBELN BUKRS LIFNR WAERS BEDAT BSART` | PO 抬头、公司、供应商、币种 |
| EKPO | `EBELN EBELP MATNR TXZ01 WERKS LGORT MENGE MEINS NETWR LOEKZ ELIKZ` | 项目、订单数量、删除/交货完成 |
| EKBE | `EBELN EBELP BEWTP VGABE BELNR GJAHR BUZEI MENGE WRBTR SHKZG` | PO 历史与回退 |
| MKPF | `MBLNR MJAHR BUDAT STBLG` | 物料凭证抬头与冲销证据 |
| MSEG | `MBLNR MJAHR ZEILE EBELN EBELP BWART MENGE SHKZG` | GR/退货/冲销净数量 |
| RBKP | `BELNR GJAHR BUKRS RBSTAT BLDAT BUDAT WAERS STBLG` | 发票抬头和过账状态 |
| RSEG | `BELNR GJAHR BUZEI EBELN EBELP MENGE WRBTR SHKZG SPGR*` | PO item 发票数量/金额与冻结原因 |
| BKPF | `BUKRS BELNR GJAHR BLART BUDAT AWTYP AWKEY STBLG` | LIV→FI 与清账/付款凭证分类 |
| BSEG | `BUKRS BELNR GJAHR BUZEI KOART LIFNR WRBTR DMBTR SHKZG AUGBL AUGGJ AUGDT ZLSPR ZFBDT REBZG REBZJ REBZZ` | 供应商未清、冻结、清账、部分付款 |

适配器可额外返回 `FAEDT` 作为已经正确计算的净到期日。没有可靠到期日时应留空，分析器就不会做逾期断言。分析器不会把 `ZFBDT` 付款基准日当作净到期日。

## SAPClaw evidence 快照

`EvidenceP2PDataSource` 接受验证编排层生成的 JSON，而不在 Assistant 进程内调用 MCP。根对象至少包含：

```json
{
  "schema_version": "1.0",
  "metadata": {
    "run_id": "...",
    "source": "sapclaw_runtime",
    "as_of": "2026-08-09"
  },
  "completeness": { "complete": true },
  "entities": {
    "A_PurchaseOrder": [],
    "A_PurchaseOrderItem": [],
    "A_MaterialDocumentHeader": [],
    "A_MaterialDocumentItem": [],
    "A_SupplierInvoice": [],
    "A_SuplrInvcItemPurOrdRef": [],
    "A_OperationalAcctgDocItemCube": []
  }
}
```

编排层在 FI 行上可添加 `SupplierInvoice`、`SupplierInvoiceFiscalYear`、`PartialPaymentReference` 和 `PartialPaymentReferenceFiscalYear` 作为确定性跨 API 关联字段。若这些字段缺失，只有单发票 PO 项目允许按 `PurchasingDocument/PurchasingDocumentItem` 保守关联；多发票项目必须提供明确关联。

所有分页必须读取完毕后才能设置 `completeness.complete=true`。快照不得包含凭据、银行字段或完整供应商敏感数据。

## 净额与状态语义

- 数量/金额优先按 `SHKZG` 净额：`S` 为正，`H` 为负。缺失借贷标识的物料移动以常见反向移动类型（102/122/124/162）兜底。
- 只有 `RBKP-RBSTAT` 为 `5`（或适配器标准化为 `POSTED`）的发票计入已过账发票；缺 RBKP 时会保守计入 RSEG，但产生数据完整性警告。
- 发票级付款比例分配给该发票的每个 PO item。完整付款清账比例为 1；部分付款比例为付款引用金额 / FI 供应商行金额。
- 有清账凭证但清账 `BKPF-BLART` 不在付款类型集合时，状态不会被错误标为“已付款”，并给出“非付款清账”解释。
- 退货、冲销、贷项都会降低净数量或金额。负净额与复杂后续借/贷应结合客户业务规则继续扩展，而不是只依赖文档存在性。

## 安全与权限

推荐使用只读技术用户和 released API/CDS。日志中只记录 PO/凭证技术键与错误码，不记录凭据、银行信息或完整供应商敏感信息。生产适配器还应实现超时、分页、限流、可观测性和字段级授权失败提示。

