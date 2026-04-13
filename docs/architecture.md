# Architecture Diagram

## 전체 흐름

```mermaid
flowchart LR
    subgraph LOCAL["💻 Local Machine"]
        CC["Claude Code\n(claude CLI)"]
        HS["Approval Server\n(Python / localhost:8080)"]
    end

    subgraph AWS["☁️ AWS"]
        APIGW["API Gateway\n(HTTPS endpoint)"]
        LMB["Lambda\n(webhook handler)"]
        DDB["DynamoDB\n(approval state)\nTTL: 10min"]
    end

    subgraph SLACK["💬 Slack"]
        BOT["Slack Bot\n(Interactive Message)"]
        USER["👤 User\n(Approve / Deny)"]
    end

    %% 1. Claude Code → 로컬 서버
    CC -- "① PermissionRequest\nHTTP hook\n{tool, input}" --> HS

    %% 2. 로컬 서버 → Slack
    HS -- "② chat.postMessage\n(Approve/Deny 버튼)" --> BOT

    %% 3. 사용자 클릭
    BOT -- "③ 버튼 표시" --> USER
    USER -- "④ 클릭" --> BOT

    %% 4. Slack → API GW → Lambda → DDB
    BOT -- "⑤ Interactive webhook\nPOST /slack/interact" --> APIGW
    APIGW -- "⑥ invoke" --> LMB
    LMB -- "⑦ PutItem\n{approval_id, decision}" --> DDB

    %% 5. 로컬 서버 polling
    HS -- "⑧ polling\nGetItem (1s interval)" --> DDB
    DDB -- "⑨ decision:\nallow / deny" --> HS

    %% 6. 결과 반환
    HS -- "⑩ HTTP response\n{behavior: allow/deny}" --> CC
```

## 컴포넌트 역할

| 컴포넌트 | 위치 | 역할 |
|---------|------|------|
| Claude Code | Local | PermissionRequest 발생 시 HTTP hook 호출 |
| Approval Server | Local | hook 수신 → Slack 전송 → DDB polling → 결과 반환 |
| API Gateway | AWS | Slack Interactive webhook 수신 (고정 HTTPS URL) |
| Lambda | AWS | webhook payload 파싱 → DDB 저장 |
| DynamoDB | AWS | approval 상태 저장 (TTL 10분 자동 삭제) |
| Slack Bot | Slack | Approve/Deny 버튼 메시지 표시 |

## 시퀀스 상세

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant HS as Approval Server (local)
    participant SL as Slack API
    participant AG as API Gateway
    participant LB as Lambda
    participant DB as DynamoDB

    CC->>HS: POST /hook (tool_name, tool_input)
    HS->>DB: PutItem (approval_id, status=pending)
    HS->>SL: chat.postMessage (버튼 포함)
    SL-->>HS: ok

    loop polling (1s, max 5min)
        HS->>DB: GetItem (approval_id)
        DB-->>HS: status=pending
    end

    Note over SL: 사용자 버튼 클릭

    SL->>AG: POST /slack/interact (payload)
    AG->>LB: invoke
    LB->>DB: UpdateItem (status=approved/denied)
    LB-->>AG: 200 OK
    AG-->>SL: 200 OK

    DB-->>HS: status=approved
    HS-->>CC: {"behavior": "allow"}
```
