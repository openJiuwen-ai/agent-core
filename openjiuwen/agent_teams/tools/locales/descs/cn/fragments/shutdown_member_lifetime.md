退场终态由成员的 lifetime 决定：常规成员落 SHUTDOWN；`create_task` 内联且 `lifetime=dormant` 的成员落 DORMANT（休眠，保留在名册）。要彻底关闭一个休眠成员，对它再调一次本工具即可直接落 SHUTDOWN；`force=true` 一律落 SHUTDOWN。
