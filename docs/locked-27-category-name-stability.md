# Locked 27 Category Name Stability (ROC 100-115)

Source: `moex_subject_catalog__y100-115.json`.

Matching rule: exact normalized category name only. `公職{target}` and `公職{target}類科` are detected as public-service variants, but they are excluded from the current professional-license ingestion scope. Substring matching is not used.

## Summary

- `中醫師(一)`: `stable`, years `101-115`, category codes `29`, rows `80`
- `中醫師(二)`: `stable`, years `102-115`, category codes `28`, rows `112`
- `公共衛生師`: `stable`, years `110-114`, category codes `5`, rows `30`
- `助產師`: `stable`, years `100-114`, category codes `21`, rows `105`
- `呼吸治療師`: `stable`, years `100-114`, category codes `25`, rows `150`
- `法醫師`: `stable_with_public_service_variant`, years `100-114`, category codes `27`, rows `150`
- `營養師`: `stable_with_public_service_variant`, years `100-115`, category codes `35`, rows `195`
- `牙醫師(一)`: `stable_paren_mixed`, years `100-115`, category codes `31`, rows `62`
- `牙醫師(二)`: `stable_paren_mixed`, years `100-115`, category codes `31`, rows `124`
- `牙體技術師`: `stable`, years `100-114`, category codes `19`, rows `114`
- `物理治療師`: `stable`, years `100-115`, category codes `31`, rows `186`
- `獸醫師`: `stable_with_public_service_variant`, years `100-114`, category codes `60`, rows `303`
- `社會工作師`: `stable_with_public_service_variant`, years `100-115`, category codes `63`, rows `347`
- `職能治療師`: `stable`, years `100-114`, category codes `25`, rows `150`
- `聽力師`: `stable`, years `100-114`, category codes `19`, rows `114`
- `臨床心理師`: `stable_with_public_service_variant`, years `100-114`, category codes `30`, rows `161`
- `藥師(一)`: `stable_paren_mixed`, years `103-115`, category codes `24`, rows `72`
- `藥師(二)`: `stable_paren_mixed`, years `104-115`, category codes `23`, rows `69`
- `語言治療師`: `stable_with_public_service_variant`, years `100-114`, category codes `20`, rows `117`
- `諮商心理師`: `stable_with_public_service_variant`, years `100-114`, category codes `28`, rows `157`
- `護理師`: `stable_with_public_service_variant`, years `100-115`, category codes `47`, rows `199`
- `醫事放射師`: `stable`, years `100-115`, category codes `31`, rows `186`
- `醫事檢驗師`: `stable_with_public_service_variant`, years `100-115`, category codes `43`, rows `210`
- `醫師(一)`: `stable`, years `100-115`, category codes `28`, rows `56`
- `醫師(二)`: `stable`, years `100-115`, category codes `31`, rows `124`
- `驗光師`: `stable`, years `106-114`, category codes `15`, rows `75`
- `驗光生`: `stable`, years `106-114`, category codes `14`, rows `56`

## Review Notes

### 法醫師
- status: `stable_with_public_service_variant`
- official category names: 法醫師 (90) | 公職法醫師 (50) | 公職法醫師類科 (10)
- official category labels: 高考_法醫師 (78) | 司法三等考試_公職法醫師 (15) | 司法三等三等考試_公職法醫師 (15) | 司法人員特考三等考試_公職法醫師 (10) | 法醫師高考_法醫師 (6) | 專技高考法醫師考試_法醫師 (6) | 司法三等考試_公職法醫師類科 (5) | 司法人員三等考試_公職法醫師類科 (5) | 司法人員三等考試_公職法醫師 (5) | 三等_公職法醫師 (5)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 營養師
- status: `stable_with_public_service_variant`
- official category names: 營養師 (189) | 公職營養師 (6)
- official category labels: 高考_營養師 (129) | 專技高考_營養師 (24) | 高等考試_營養師 (12) | 高考三級_公職營養師 (6) | 高考營養師專技高考_營養師 (6) | 營養師高考_營養師 (6) | 高考營養師_營養師 (6) | 專技高考營養師考試_營養師 (6)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 牙醫師(一)
- status: `stable_paren_mixed`
- official category names: 牙醫師(一) (40) | 牙醫師（一） (22)
- official category labels: 專技高考_牙醫師（一） (22) | 專技高考_牙醫師(一) (18) | 高等考試_牙醫師(一) (12) | 高等_牙醫師(一) (10)
- parentheses mixed: `True`
- public-service variant: `False`
- current scope: `included if professional-license exam`

### 牙醫師(二)
- status: `stable_paren_mixed`
- official category names: 牙醫師(二) (80) | 牙醫師（二） (44)
- official category labels: 專技高考_牙醫師（二） (44) | 專技高考_牙醫師(二) (36) | 高等_牙醫師(二) (24) | 高等考試_牙醫師(二) (20)
- parentheses mixed: `True`
- public-service variant: `False`
- current scope: `included if professional-license exam`

### 獸醫師
- status: `stable_with_public_service_variant`
- official category names: 獸醫師 (150) | 公職獸醫師 (149) | 公職獸醫師類科 (4)
- official category labels: 專技高考_獸醫師 (114) | 高考三級_公職獸醫師 (66) | 三等_公職獸醫師 (45) | 三等考試_公職獸醫師 (25) | 高等_獸醫師 (24) | 高等考試_獸醫師 (12) | 高考一級_公職獸醫師 (6) | 原住民族三等考試_公職獸醫師 (5) | 地方政府公務人員三等_公職獸醫師類科 (2) | 地方政府公務人員考試三等_公職獸醫師 (2) | 三等_公職獸醫師類科 (2)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 社會工作師
- status: `stable_with_public_service_variant`
- official category names: 社會工作師 (211) | 公職社會工作師 (130) | 公職社會工作師類科 (6)
- official category labels: 高考_社會工作師 (171) | 高考三級_公職社會工作師 (66) | 三等_公職社會工作師 (45) | 三等考試_公職社會工作師 (15) | 高等考試_社會工作師 (14) | 專技高考_社會工作師 (12) | 高考社會工作師專技高考_社會工作師 (7) | 高考社工師_社會工作師 (7) | 地方政府公務人員三等_公職社會工作師類科 (2) | 離島地區公務人員考試三等_公職社會工作師類科 (2) | 地方政府公務人員考試三等_公職社會工作師 (2) | 離島地區公務人員考試三等_公職社會工作師 (2) | 三等_公職社會工作師類科 (2)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 臨床心理師
- status: `stable_with_public_service_variant`
- official category names: 臨床心理師 (153) | 公職臨床心理師 (8)
- official category labels: 高考_臨床心理師 (99) | 專技高考_臨床心理師 (12) | 高等_臨床心理師 (12) | 高考三級_公職臨床心理師 (8) | 高考心理師專技高考_臨床心理師 (6) | 心理師高考_臨床心理師 (6) | 高考心理師_臨床心理師 (6) | 專技高考心理師考試_臨床心理師 (6) | 高等考試_臨床心理師 (6)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 藥師(一)
- status: `stable_paren_mixed`
- official category names: 藥師(一) (60) | 藥師（一） (12)
- official category labels: 專技高考_藥師(一) (27) | 高等考試_藥師(一) (18) | 高等_藥師(一) (15) | 專技高考_藥師（一） (12)
- parentheses mixed: `True`
- public-service variant: `False`
- current scope: `included if professional-license exam`

### 藥師(二)
- status: `stable_paren_mixed`
- official category names: 藥師(二) (60) | 藥師（二） (9)
- official category labels: 專技高考_藥師(二) (27) | 高等_藥師(二) (18) | 高等考試_藥師(二) (15) | 專技高考_藥師（二） (9)
- parentheses mixed: `True`
- public-service variant: `False`
- current scope: `included if professional-license exam`

### 語言治療師
- status: `stable_with_public_service_variant`
- official category names: 語言治療師 (112) | 公職語言治療師 (5)
- official category labels: 高考_語言治療師 (76) | 專技高考_語言治療師 (6) | 相當高考語言治療師相當高考_語言治療師 (6) | 語言治療師高考_語言治療師 (6) | 語言治療師相當高考_語言治療師 (6) | 專技高考語言治療師_語言治療師 (6) | 語言治療師_語言治療師 (6) | 三等考試_公職語言治療師 (5)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 諮商心理師
- status: `stable_with_public_service_variant`
- official category names: 諮商心理師 (153) | 公職諮商心理師 (4)
- official category labels: 高考_諮商心理師 (99) | 專技高考_諮商心理師 (12) | 高等_諮商心理師 (12) | 高考心理師專技高考_諮商心理師 (6) | 心理師高考_諮商心理師 (6) | 高考心理師_諮商心理師 (6) | 專技高考心理師考試_諮商心理師 (6) | 高等考試_諮商心理師 (6) | 高考三級_公職諮商心理師 (4)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 護理師
- status: `stable_with_public_service_variant`
- official category names: 護理師 (175) | 公職護理師 (24)
- official category labels: 高考_護理師 (135) | 高考三級_公職護理師 (24) | 高等考試_護理師 (10) | 專技高考_護理師 (10) | 高考醫事人員專技高考_護理師 (5) | 醫事人員高考高考_護理師 (5) | 高考醫事人員_護理師 (5) | 專技高考醫事人員_護理師 (5)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

### 醫事檢驗師
- status: `stable_with_public_service_variant`
- official category names: 醫事檢驗師 (186) | 公職醫事檢驗師 (24)
- official category labels: 專技高考_醫事檢驗師 (90) | 高等考試_醫事檢驗師 (36) | 高等_醫事檢驗師 (36) | 高考三級_公職醫事檢驗師 (24) | 高考醫事人員專技高考_醫事檢驗師 (6) | 醫事人員高考高考_醫事檢驗師 (6) | 高考醫事人員_醫事檢驗師 (6) | 專技高考醫事人員_醫事檢驗師 (6)
- parentheses mixed: `False`
- public-service variant: `True`
- current scope: `excluded`

