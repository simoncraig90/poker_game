# Opcode Catalog

Session: captures/20260329_202750 | 733 frames | 8 hands

## Message Types

| Opcode | Count | Dir | Size | Event Type | Description |
|--------|-------|-----|------|-----------|-------------|
| 0x04 | 2 | recv | 212 | OTHER_0x04 | Init data (lobby config) |
| 0x34 | 8 | recv | 136 | HAND_SUMMARY | Hand summary: winner seat, pot, showdown flag, hand rank |
| 0x36 | 3 | recv | 588-612 | OTHER_0x36 | URL push / resource load |
| 0x37 | 2 | recv | 68 | OTHER_0x37 | Config update |
| 0x3f | 1 | recv | 72 | OTHER_0x3f | Unknown (table join ack?) |
| 0x48 | 8 | recv | 100 | TABLE_CONFIG | Table config / bet increments |
| 0x4b | 2 | recv | 40-80 | OTHER_0x4b | Table state transition |
| 0x51 | 5 | recv | 12 | UNKNOWN_0x51 | Unknown (12-byte marker) |
| 0x59 | 1 | recv | 16 | OTHER_0x59 | Unknown (ack?) |
| 0x5a | 7 | recv | 56-148 | DEAL_NOTIFY | Deal notification — visible cards (river, hero) in F2 |
| 0x60 | 1 | recv | 160 | OTHER_0x60 | Seat map / player layout |
| 0x64 | 2 | sent | 72-108 | RAW_0x64 | Client join / session setup |
| 0x65 | 1 | sent | 108 | OTHER_0x65 | Client leave table |
| 0x6a | 1 | recv | 2192 | TABLE_SNAPSHOT | Full table snapshot on join (seats, blinds, config) |
| 0x6b | 1 | recv | 64 | OTHER_0x6b | Table close notification |
| 0x6c | 150 | recv | 244-292 | PLAYER_UPDATE | Player state update (stack, status, bet, cards) |
| 0x6d | 45 | sent/recv | 16-104 | HEARTBEAT_ACK | Heartbeat ack (16B) or NEW HAND (>16B, has hand ID + button) |
| 0x6e | 9 | recv/sent | 24-56 | HAND_BOUNDARY | Hand boundary marker (between hands) |
| 0x6f | 64 | recv | 72 | PLAYER_TIMER | Per-player action countdown timer |
| 0x70 | 8 | recv | 152-192 | BET_ORDER | Seat betting order for current round |
| 0x71 | 7 | recv | 56-176 | DEAL_BOARD | Community cards dealt (F2 = card array: flop 3, turn/river 1) |
| 0x72 | 72 | recv | 80 | ROUND_TRANSITION | Round transition: active seat, round ID, bet-to-call |
| 0x73 | 17 | sent/recv | 36-72 | JOIN_REQUEST | Action timer tick / join request (if amount > 100) |
| 0x74 | 3 | recv | 80 | TIME_BANK | Time bank activation |
| 0x75 | 1 | recv | 60 | HAND_COMPLETE | Hand processing complete signal |
| 0x76 | 49 | recv | 80 | STACK_UPDATE | Stack size update after chip movement |
| 0x77 | 64 | recv/sent | 56-112 | ACTION | Player action: fold/check/call/bet/raise (F2=seat, F3=amt, F4=delta) |
| 0x78 | 62 | recv | 88-128 | POT_UPDATE | Pot update: main + side pot amounts |
| 0x79 | 8 | recv | 108 | SEAT_BETS | Per-seat bet chip display (6 values) |
| 0x7a | 11 | sent | 84 | OTHER_0x7a | Client action response (sent by hero) |
| 0x7b | 8 | recv | 92 | POT_AWARD | Pot award: who wins how much from each pot |
| 0x7c | 8 | recv | 72 | ROUND_BET_SUMMARY | Round bet summary: per-player investment |
| 0x7d | 7 | recv | 356-428 | HAND_RESULT | Hand result with human-readable text descriptions |
| 0x83 | 37 | recv | 16 | HEARTBEAT_PING | Server heartbeat ping (incrementing seq number) |
| 0x87 | 1 | recv | 272 | OTHER_0x87 | Table init / first connection data |
| 0x88 | 1 | recv | 32 | OTHER_0x88 | Unknown (post-join?) |
| 0x8b | 18 | recv | 100-408 | HERO_CARDS | Hero hole cards in F3 (card structs with F1=0 = visible) |
| 0x8d | 11 | recv | 116-168 | ACTION_PROMPT | Action prompt: available actions + raise min/max/stack |
| 0x8f | 18 | recv | 56-116 | UNKNOWN_0x8f | Unknown (near action prompts) |
| 0xad | 1 | sent | 288 | OTHER_0xad | Client message (post-action?) |
| 0xb2 | 1 | sent | 428 | OTHER_0xb2 | Join table confirmation / seat assignment |
| 0xba | 1 | recv | 72 | OTHER_0xba | Unknown (rare) |
| 0xc2 | 4 | sent | 72 | OTHER_0xc2 | Client message (ack?) |
| 0xc6 | 1 | sent | 16 | OTHER_0xc6 | Client message (ack?) |
| 0xc7 | 1 | sent | 60 | OTHER_0xc7 | Client message (rewards widget?) |

## Card Encoding

Cards are Thrift structs: `{F1: visibility, F2: suit, F3: rank}`

- **F1**: 0 = visible to hero, 255 = hidden (opponent's card)
- **F2 (suit)**: 1=clubs, 2=diamonds, 3=hearts, 4=spades
- **F3 (rank)**: 2-10 numeric, 11=J, 12=Q, 13=K, 14=A

Sources:
- 0x8b F3: Hero hole cards (F1=0, visible)
- 0x71 F2: Community cards (flop=3, turn=1, river=1; F1=255 if opponent perspective)
- 0x5a F2: Additional visible cards (river, sometimes hero cards)

## Key Field Map

| Concept | Opcode | Field | Type | Unit |
|---------|--------|-------|------|------|
| Table ID | all game | F1 | string | `6R.{id}` |
| Table name | 0x6a | F2.F1 | string | |
| Blinds | 0x6a | F2.F7.F1 (SB), F2.F7.F2 (BB) | i32 | cents |
| Player name | 0x6a/6c | seat.F3.F1 | string | |
| Seat number | many | seat.F1, or F2 | byte | 0-5 |
| Stack | 0x6a/6c/76 | seat.F3.F7, or F3 | i32 | cents |
| Action seat | 0x77 | F2 | byte | 0-5 |
| Action amount | 0x77 | F3 | i32 | cents |
| Action delta | 0x77 | F4 | i32 | cents (neg=collect sweep) |
| Pot | 0x78 | F2[0].F1 | i32 | cents |
| Win amount | 0x7b/7d | F3[n].F2 or F3[n].F3 | i32 | cents |
| Hand ID | 0x6d(large) | F2 | string | numeric |
| Button | 0x6a/6d | F7 or F5 | byte | 0-5 |
| Round/street | 0x72 | F3 | i32 | see below |
| Showdown? | 0x34 | F5[3] | string | "true"/"false" |
| Result text | 0x7d | F3[n].F5 | string | |

## Round ID Values (0x72 F3)

| Value | Context |
|-------|---------|
| 3-5 | Preflop action positions (sequential seats) |
| 10 | Fold / check / pass (no action needed) |
| 11 | New street opens (first action on flop/turn/river) |
| 12 | Call (facing a bet) |
| 13 | Bet / all-in |
| 15 | Raise made |
| 22 | Hand setup / pre-deal |

## Serialization

Apache Thrift Binary Protocol. Frame: byte[0]=0x00 (framing), byte[1]=opcode, byte[2+]=fields.
Field header: 1 byte type + 2 byte field ID (big-endian). Struct ends with 0x00 stop byte.
Types: 0x02=bool, 0x03=byte, 0x06=i16, 0x08=i32, 0x0a=i64, 0x0b=string, 0x0c=struct, 0x0f=list, 0x0d=map.
