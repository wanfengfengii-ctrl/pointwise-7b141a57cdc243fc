# 河网巡检交班档案

河网巡检资料组的采集员按班次从本地巡检门户领取 CSV。值班员在取件期间仍可改选共享的区域和季度。接纳员核对每次导出的实际渲染条件后归档。班长在门户停用后领取明细和首执，用缺测数安排补测。

## 运行

使用 Python 3.11 和标准库，在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B -m archive.run demo
```

`check_project.py` 是工程物化时提供的标准回归入口。直接运行 `python3 -B -m unittest discover -s tests -t . -v` 也可执行完整原套件。默认 demo 在一次性工作区运行。要保存原件及档案，可指定工作区。

```sh
python3 -B -m archive.run init workspace
python3 -B -m archive.run demo --workspace workspace
```

初始化建立 `capture.sqlite3` 与 `archive.sqlite3`，并复制缺失的 `records.csv` 和 `day.json`。重复初始化保留用户修改的输入、采录件、待核、拒收和正式档案。用新工作区可从空库重演。样例中的西区二、三季度给出相同 CSV。按二季度实际渲染的工单签出 `1250 Pa`、`1.25 kPa` 和一条缺测。下载前切回二季度的三季度导出仍被拒收。清单按照班内位置显示正式工单。

## 门户与取件

`portal.records.load_records(path)` 加载源序记录。样例源表列为 `region,quarter,inspection_id,pressure_pa`。区域为 `east`、`west`、`north` 或 `south`，季度为 1 到 4。

- `portal.render.Portal(portal_id, records, region, quarter)` 启动一次本地门户会话。`portal.view.edit_filter(portal.view, field, value, actor, expected_version=None)` 修改单项并返回 `FilterPosition`。同值改选也推进版本。非法参数抛出 `ValueError`，预期版本不一致抛出 `VersionConflict`。`read_filter(portal.view, field)` 读取该项最新写入。
- `request_export(portal, export_id)` 受理请求，`render_export(portal, export_id)` 按实际渲染时的视图固定文件，`render_download(portal, export_id)` 取回原字节及实渲染锚点，未渲染返回 `None`。未知、关闭、重复渲染和不可取得由 `PortalError.code` 区分。每次新渲染使用该门户下全新的导出号。
- `capture.orders.make_order(order_id, portal_id, region, quarter, shift, position)` 建立固定工单。班次采用 `YYYY-MM-DD/day` 或 `YYYY-MM-DD/night`，同班位置是唯一的正整数。工单、门户和导出标识使用 1 到 96 个字母、数字、点、下划线或连字符，首位是字母或数字。
- `capture.collect.collect_order(spool, portal, order, export_id, drive=None, *, before_restore=None)` 执行暂借及取件。`drive` 是录制事件的驱动函数，接收门户和导出号，省略时直接实际渲染。`before_restore` 可在条件归还前插入录制的人工改选。成功返回 `ready` 及 `Delivery`，失败返回 `failed` 及原因。退出时处理所有已成功暂写的项。同值人工接手与归还间的人工改选均保留。`capture.spool.load_delivery(spool, portal_id, export_id)` 可在关停后重读原件和依据。已经保全的同号同工单材料沿原绑定返回。

## 实收、换算与复领

- 用 `archive.store.open_archive(path)` 打开归档。自行装配时调用 `intake.store.ensure_intake_schema` 建好实收表，并在采录连接上调用 `capture.spool.ensure_delivery_schema`。
- `intake.files.read_received_file(order_id, portal_id, export_id, content)` 建立文件来件。`intake.context.read_received_context` 读取采录生成的 `RenderEvidence` 或其字典表示。输入来自本地受控的采录端。文件摘要用于核对原字节，材料另按工单、门户、导出及实渲染位置关联。
- `intake.receive.receive_material(db, order, export_id, material=None, *, process=True)` 每次登记文件或来源依据。缺件为 `pending`。不同导出或摘要的补件返回 `rejected`，先到件仍可接收自己的补件。实际区域或季度不符返回 `EXPORT_CONTEXT_MISMATCH`。`process=False` 可只持久登记来件，之后省略 `material` 对已登记件续办。归档实收和续办只使用已经保全的材料。
- `measurement.validation.parse_measurements(content, portal_id, export_id)` 校验表头 `inspection_id,pressure_pa` 和全批数据。记录顺序按 CSV 数据记录计数，每条保留一基序号、原身份与压力原字段。压力仅允许空字段或带可选正负号的十进制整数 Pa。空值计作缺测，零仍为已测。kPa 用精确十进制表示。错误由 `MeasurementError` 给出，首个坏行拒绝整批。
- `archive.publish.publish_archive(db, order, evidence, batch)` 是接纳使用的发布接口，参数为已获资格的同次证据及完整核验批。整个成果和首执一起提交。`receive_material` 的 `accepted` 携带 `result_id` 与首执。提交失败返回 `failed` 与 `PUBLICATION_FAILED`，材料仍可续办。合法同件重投返回相同成果和首执。
- `archive.shift.shift_manifest(db, shift)` 按班内位置列出正式件。合法空班返回空元组，非法班次抛出 `ValueError`。`archive.history.get_archive(db, result_id)` 返回固定明细、缺测、出处和首执，未知成果号返回 `None`。Python API 提供整型 Pa 与 `Decimal` kPa，demo JSON 中两种读值均用精确文本表示。
