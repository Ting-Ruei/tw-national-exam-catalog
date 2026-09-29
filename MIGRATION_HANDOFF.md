# Retired runtime handoff

本文件原先描述一個已移出的外部 runtime 與 SQL review deployment。那套設計不再成立，
也不屬於目前 catalog 的 active 路由；其中的 host、port、writer、資料庫與 rollback
敘述不得再被當成操作指令。

目前只使用：

1. `qbr/`：官方 PDF → 可驗 package，不寫資料庫。
2. `review_ui/v2.html`：本機 review queue 的人工審題介面。
3. 當次任務明確指定的本機模型與本機 review store。

若未來需要新的模型節點、SQL backend、遠端審題或 production deployment，請建立全新
owner-approved contract，重新定義 endpoint、身份、writer、artifact、驗證與 rollback。
不要從本文件、舊 deployment copy 或 Git history 直接恢復服務。
