向当前群发表公开消息，发送者、团队和 session 由运行时绑定。content 是正文，client_message_id 是稳定唯一 ID；重试复用原记录。mentions 填准确的成员标识。所有消息都广播保存，只有被 @ 的成员收到近期 5 条摘录和 history.jsonl 路径；没有 mentions 时不触发模型输入。普通定向消息和内部广播使用 send_message。
