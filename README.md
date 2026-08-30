# 智能心盾

面向心脏破裂风险资料复核、急诊就诊概览、辅助诊断和病情过程追踪的临床辅助分析系统。

## 数据边界

系统运行时只读项目根目录的 clean_非破裂完整版（15天窗口）.xlsx，不会修改原始工作簿，也不会生成模拟患者数据。当前工作簿清洗后的数据基线为：

- 5348 名患者（按 regno 去重）
- 14419 条就诊记录（按 regno + admno 区分）
- 5063 名患者存在多次就诊
- 0 条记录的 label=1；有效记录均为回顾性非破裂样本
- 1807 条就诊记录包含可确认的手术或介入记录

label 仅作为工作簿中的回顾性目标事件标签展示，不等同于预测概率、风险等级、死亡结局或实时预警。cutoff_time 仅表示 15 天数据窗口截止时间，不是预测破裂时间。工作簿本身没有可验证的模型风险分层或预测时间字段。尚未调用预测模型的记录统一显示“无法判断”和“暂无模型结果”，不会使用 label、cutoff_time 或规则推造风险；模型真实调用成功后，结果仅保存在当前网页会话中并展示在对应 `regno + admno` 就诊范围内。

工作簿路径可通过环境变量或 .streamlit/secrets.toml 覆盖：

    PATIENT_WORKBOOK_XLSX = "D:/path/to/clean_非破裂完整版（15天窗口）.xlsx"

## 页面

- 首页：系统用途、数据边界与三项主要功能入口。
- 急诊概览：患者/就诊统计、组合筛选、排序、分页和就诊级详情跳转。
- 辅助诊断：按患者与就诊筛选，分组核对诊断、检查检验、用药医嘱、手术、病程与风险字段。
- 病情详情：严格按 regno + admno 展示单次就诊，并按真实时间字段生成时间轴。
- 关于：系统说明、数据来源和临床安全边界。

## 启动

在项目目录执行：

    python -m pip install -r requirements.txt
    python -m streamlit run app.py

浏览器打开 http://localhost:8501。

## 验证

    python -m py_compile app.py config.py agent\*.py components\*.py services\*.py views\*.py
    python -m unittest discover -s tests -v

## Agent 与预测模型配置

项目已合入 `Xindun` 中的模型调用部分：百炼 ReAct Agent、通用医学知识模型、预测截点前临床资料整理、心脏破裂预测模型双地址容错客户端和可续跑的批量预测管线。实际 Qwen2.5-7B 模型权重位于 `D:/Personal/Desktop/xinzangpolie/model`，训练评估代码使用 vLLM 双卡推理；当前网页不把 15GB 权重直接载入 Streamlit 进程，而是通过 OpenAI 兼容推理服务调用。

将 `.streamlit/secrets.toml.example` 复制为 `.streamlit/secrets.toml` 后配置：

- `DASHSCOPE_API_KEY`：百炼 Agent 与知识模型密钥。
- `BAILIAN_MODEL`：主 Agent，默认 `qwen3.7-plus`。
- `BAILIAN_KNOWLEDGE_MODEL`：医学知识说明模型，默认 `deepseek-v4-flash`。
- `CARDIAC_RISK_URLS`：预测模型服务地址，可配置两个地址进行故障切换。
- `CARDIAC_RISK_MODEL`：预测服务中的模型名称，默认 `cardiac-rupture-qwen38`。
- `PREDICTION_RESULTS_PATH`：批量结果 JSONL；不配置时按当前工作簿名保存到 `model-results/`。

部署到服务器时，`127.0.0.1` 表示“网页所在服务器本机”。如果预测模型运行在另一台机器，必须将 `CARDIAC_RISK_URLS` 改成网页服务器能够访问的内网地址，并在网络与防火墙中开放相应端口。

模型输入由程序按当前就诊记录整理，只使用 `cutoff_time` 前 15 天内的记录，按预测截点前 0～48 小时、48～72 小时、72～360 小时分组，并排除 regno、admno、label、预测截点后信息及无法可靠配对的聚合事件。上传模型的真实输出契约是“会发生 / 未发生心脏破裂”二分类与文字解释，不提供校准概率、可信度或具体破裂时间；页面不会补造这些字段。

在具备两张足够显存 GPU 的 Linux 服务器上，可按原评估代码的模型路径启动兼容服务（端口可分别设置为 8000、8001）：

    python -m vllm.entrypoints.openai.api_server --model /path/to/xinzangpolie/model --served-model-name cardiac-rupture-qwen38 --tensor-parallel-size 2 --dtype bfloat16 --max-model-len 8192 --port 8000

项目也提供 `scripts/start_prediction_service.sh`。设置 `CARDIAC_RISK_MODEL_PATH` 后即可启动同样的服务；如需第二个副本，可设置 `CARDIAC_RISK_PORT=8001` 后另行启动。

服务上线后，进入“急诊概览 → 预测模型运行”，先预测100条验证连通性，再选择全部记录。任务在独立进程中运行，结果逐条追加保存，意外中断后可续跑；页面会按 encounter_key 将结果回填到急诊概览、辅助诊断和病情详情。

不要将 .streamlit/secrets.toml 或原始工作簿提交到公开仓库。共享密码不能替代正式医院系统所需的身份认证、最小权限和审计。

## 安全边界

本系统仅用于临床辅助和科研分析，不能替代医生诊断、处置或治疗决策。所有风险结果都需要结合完整临床资料由专业医护人员复核。
