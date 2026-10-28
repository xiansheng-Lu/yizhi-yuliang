-- 只扩受控场景枚举；不修改0069或现有请求/提示/模板版本。
ALTER TABLE content_ai_requests DROP CONSTRAINT content_ai_requests_ai_scenario_check;
ALTER TABLE content_ai_requests ADD CONSTRAINT content_ai_requests_ai_scenario_check
 CHECK(ai_scenario IN ('content_topic','content_planning','content_script','content_storyboard'));
