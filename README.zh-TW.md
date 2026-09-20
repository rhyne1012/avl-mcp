# avl-mcp

透過 MCP 執行 AVL 3.52 氣動分析。第一階段提供五個工具：
`avl.health`、`avl.inspect`、`avl.validate`、`avl.run`、`avl.sweep`。

目前版本為 `0.1.0a1`，本機測試平台為 macOS Apple Silicon。
安裝與完整工具說明見 [英文 README](README.md)，驗證範圍見
[驗證紀錄](docs/validation.md)。

## 工作方式

每次分析建立獨立工作目錄，保留原始輸入副本、實際送入 AVL 的指令、
求解器版本與雜湊、原始結果、終端日誌及解析後的 JSON／CSV。
所有結果放在啟動時指定的 `--work-root`；Python 環境與原生函式庫保持在本機。

單點分析及掃描使用指定飛行條件，不自動載入旁邊同名的 `.run`／`.mass`。
第一階段尚未提供配平、模態、幾何編輯或 OpenVSP 自動轉換。
`avl.validate` 檢查文字結構、依賴檔案及基本幾何條件，不代表已完成幾何干涉檢查。

## 必須保留的物理定義

- 幾何座標：X 向後、Y 向右、Z 向上。
- 輸入角速度：標準機體軸 X 向前、Y 向右、Z 向下，以 pb/(2V)、qc/(2V)、rb/(2V) 表示。
- 攻角與側滑角輸入為度；ST 表內相應導數為每弧度。
- 控制輸入是幾何檔的 CONTROL 變數值；局部偏角（度）＝變數值 × 該截面的 gain。
- CL 與 Cl 分別是升力與滾轉力矩，大小寫不能混淆。
- 力矩參考點不一定是實機重心。總阻力、近場誘導阻力、Trefftz 誘導阻力分別保留。
- `length_unit` 只標示單位，不會改變或轉換幾何尺寸。

## 驗證與限制

驗證採官方 Plane Vanilla 及 Bubble Dancer 幾何，涵蓋原生無視窗執行、
資料解析、導數有限差分核對、來源保留、失敗處理與真正的 MCP stdio 呼叫。
這些驗證建立軟體流程與數值一致性，不代表實機氣動精度。

AVL 適用於薄升力面、細長體及準定常分析，不承諾失速、分離或跨音速精度。
HS_UAV 與 VSPAERO 的同幾何比對另行進行，本程式庫不包含研究模型或 CFD 原始資料。

## 授權

本專案採 GPL-2.0-or-later。AVL 原始程式作者為 Mark Drela 與 Harold Youngren。
Python wheel 不包含 AVL 執行檔；原生程式與函式庫須在各台電腦分別安裝。

