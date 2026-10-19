//+------------------------------------------------------------------+
//| ORB_RestartSafe.mq5                                              |
//| Opening Range Breakout EA with restart-safe state recovery       |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "Opening Range Breakout: Buy Stop and Sell Stop around a time range, OCO,"
#property description "SL at the opposite side of the range, TP in R, optional break-even."
#property description "State is rebuilt from the trade server on every tick, so a terminal or VPS"
#property description "restart never creates duplicate orders."

#include <Trade\Trade.mqh>

//--- enums
enum ENUM_LOT_MODE
  {
   LOT_FIXED = 0,   // Fixed lots
   LOT_RISK  = 1    // Risk % of balance
  };

enum ENUM_TRADE_DIR
  {
   DIR_BOTH  = 0,   // Long and short
   DIR_LONG  = 1,   // Long only
   DIR_SHORT = 2    // Short only
  };

enum ENUM_BEYOND
  {
   BEYOND_WAIT   = 0, // Wait until price is back inside (until deadline)
   BEYOND_MARKET = 1  // Enter at market
  };

//--- inputs
input group "Range (broker server time, HH:MM)"
input string InpRangeStart       = "08:00";  // Range start
input string InpRangeEnd         = "08:30";  // Range end
input int    InpMaxPlaceDelayMin = 30;       // Stop placing orders N minutes after range end
input string InpCancelTime       = "12:00";  // Cancel untriggered orders at (empty = end of day)
input string InpCloseTime        = "";       // Close open position at (empty = never)
input bool   InpMon = true;                  // Trade Monday
input bool   InpTue = true;                  // Trade Tuesday
input bool   InpWed = true;                  // Trade Wednesday
input bool   InpThu = true;                  // Trade Thursday
input bool   InpFri = true;                  // Trade Friday

input group "Orders"
input ENUM_TRADE_DIR InpDirection      = DIR_BOTH;    // Direction
input double         InpOffsetPoints   = 20;          // Offset from range high/low (points)
input ENUM_BEYOND    InpIfBeyond       = BEYOND_WAIT; // If price is already beyond the level
input double         InpMinRangePoints = 0;           // Min range size (points, 0 = off)
input double         InpMaxRangePoints = 0;           // Max range size (points, 0 = off)

input group "Risk"
input ENUM_LOT_MODE InpLotMode     = LOT_RISK; // Position size mode
input double        InpFixedLots   = 0.10;     // Fixed lots
input double        InpRiskPercent = 1.0;      // Risk per trade (% of balance)
input double        InpTakeProfitR = 1.5;      // Take profit (R multiple)

input group "Filters"
input int InpMaxSpreadPoints = 50;   // Max spread (points, 0 = off)

input group "Break-even"
input bool   InpUseBreakEven   = false; // Move SL to break-even
input double InpBETriggerR     = 1.0;   // Trigger at profit (R multiple)
input double InpBEOffsetPoints = 5;     // Lock-in beyond entry (points)

input group "General"
input ulong InpMagic          = 260417; // Magic number
input int   InpSlippagePoints = 20;     // Max slippage for market orders (points)
input bool  InpLogToFile      = true;   // Write decisions to MQL5/Files log

//--- globals
CTrade   trade;
int      g_rangeStartSec = -1;
int      g_rangeEndSec   = -1;
int      g_cancelSec     = -1;
int      g_closeSec      = -1;
datetime g_lastSend      = 0;
string   g_lastLog       = "";
string   g_logFile       = "";

//--- snapshot of everything this EA owns for the current day, read from the server
struct DayState
  {
   bool   buyPending;
   bool   sellPending;
   ulong  buyTicket;
   ulong  sellTicket;
   bool   buyPlacedToday;   // pending now or found in today's order history
   bool   sellPlacedToday;
   bool   positionOpen;
   ulong  posTicket;
   bool   enteredToday;     // an entry deal exists today
  };

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
int ParseHHMM(const string s)
  {
   string t = s;
   StringTrimLeft(t);
   StringTrimRight(t);
   if(t == "")
      return -1;
   string parts[];
   if(StringSplit(t, ':', parts) != 2)
      return -2;
   int h = (int)StringToInteger(parts[0]);
   int m = (int)StringToInteger(parts[1]);
   if(h < 0 || h > 23 || m < 0 || m > 59)
      return -2;
   return h * 3600 + m * 60;
  }

datetime DayStart(const datetime t)
  {
   return t - (t % 86400);
  }

void Log(const string msg, const bool onlyIfNew = false)
  {
   if(onlyIfNew && msg == g_lastLog)
      return;
   g_lastLog = msg;
   string line = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + " [ORB " + _Symbol + "] " + msg;
   Print(line);
   if(!InpLogToFile)
      return;
   int h = FileOpen(g_logFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

double Pt()
  {
   return SymbolInfoDouble(_Symbol, SYMBOL_POINT);
  }

double RoundToTick(const double price)
  {
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0)
      tick = Pt();
   return NormalizeDouble(MathRound(price / tick) * tick, _Digits);
  }

bool IsTradingDay(const datetime day)
  {
   MqlDateTime dt;
   TimeToStruct(day, dt);
   switch(dt.day_of_week)
     {
      case 1: return InpMon;
      case 2: return InpTue;
      case 3: return InpWed;
      case 4: return InpThu;
      case 5: return InpFri;
     }
   return false;
  }

string DayTag(const datetime day)
  {
   return "ORB " + TimeToString(day, TIME_DATE);
  }

//+------------------------------------------------------------------+
//| Read our orders, positions and today's history from the server   |
//+------------------------------------------------------------------+
void ScanState(const datetime day, const bool withHistory, DayState &st)
  {
   ZeroMemory(st);

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || (ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic)
         continue;
      ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(setup < day)
        {
         // a pending order left over from a previous session: never carry it into a new day
         if(trade.OrderDelete(ticket))
            Log(StringFormat("Deleted stale pending order #%I64u from a previous day", ticket));
         continue;
        }
      if(type == ORDER_TYPE_BUY_STOP)
        {
         st.buyPending = true;
         st.buyTicket  = ticket;
        }
      if(type == ORDER_TYPE_SELL_STOP)
        {
         st.sellPending = true;
         st.sellTicket  = ticket;
        }
     }
   st.buyPlacedToday  = st.buyPending;
   st.sellPlacedToday = st.sellPending;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      st.positionOpen = true;
      st.posTicket    = ticket;
     }

   if(!withHistory)
      return;
   if(!HistorySelect(day, TimeCurrent() + 60))
      return;

   for(int i = HistoryOrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = HistoryOrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(HistoryOrderGetString(ticket, ORDER_SYMBOL) != _Symbol || (ulong)HistoryOrderGetInteger(ticket, ORDER_MAGIC) != InpMagic)
         continue;
      if((datetime)HistoryOrderGetInteger(ticket, ORDER_TIME_SETUP) < day)
         continue;
      ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)HistoryOrderGetInteger(ticket, ORDER_TYPE);
      if(type == ORDER_TYPE_BUY_STOP || type == ORDER_TYPE_BUY)
         st.buyPlacedToday = true;
      if(type == ORDER_TYPE_SELL_STOP || type == ORDER_TYPE_SELL)
         st.sellPlacedToday = true;
     }

   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol || (ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagic)
         continue;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
         st.enteredToday = true;
     }
  }

//+------------------------------------------------------------------+
//| Range from M1 bars: can be rebuilt at any time after a restart   |
//+------------------------------------------------------------------+
bool GetRange(const datetime day, double &hi, double &lo, int &bars)
  {
   datetime from = day + g_rangeStartSec;
   datetime to   = day + g_rangeEndSec - 1;
   MqlRates r[];
   bars = CopyRates(_Symbol, PERIOD_M1, from, to, r);
   if(bars <= 0)
      return false;
   hi = r[0].high;
   lo = r[0].low;
   for(int i = 1; i < bars; i++)
     {
      hi = MathMax(hi, r[i].high);
      lo = MathMin(lo, r[i].low);
     }
   return hi > lo;
  }

//+------------------------------------------------------------------+
//| Position size                                                    |
//+------------------------------------------------------------------+
double NormalizeLots(const double lots)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0)
      step = 0.01;
   double v = MathFloor(lots / step + 1e-9) * step;
   if(v < vmin)
      return 0.0;
   return NormalizeDouble(MathMin(v, vmax), 8);
  }

double CalcLots(const bool isBuy, const double entry, const double sl)
  {
   if(InpLotMode == LOT_FIXED)
      return NormalizeLots(InpFixedLots);
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   double pl = 0.0;
   if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, entry, sl, pl) || pl == 0.0)
     {
      double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
      if(tickSize <= 0 || tickValue <= 0)
         return 0.0;
      pl = MathAbs(entry - sl) / tickSize * tickValue;
     }
   double lossPerLot = MathAbs(pl);
   if(lossPerLot <= 0)
      return 0.0;
   return NormalizeLots(riskMoney / lossPerLot);
  }

//+------------------------------------------------------------------+
//| Order actions                                                    |
//+------------------------------------------------------------------+
void DeletePending(const ulong ticket, const string reason)
  {
   if(ticket == 0)
      return;
   if(trade.OrderDelete(ticket))
      Log(StringFormat("Deleted pending #%I64u (%s)", ticket, reason));
   else
      Log(StringFormat("Delete #%I64u failed: %u %s", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription()), true);
  }

// Places one side. Returns true if an order was sent successfully.
bool PlaceSide(const bool isBuy, const datetime day, const double hi, const double lo)
  {
   double off   = InpOffsetPoints * Pt();
   double level = RoundToTick(isBuy ? hi + off : lo - off);
   double sl    = RoundToTick(isBuy ? lo - off : hi + off);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double minGap = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * Pt();
   string side  = isBuy ? "BUY" : "SELL";

   bool pendingValid = isBuy ? (level > ask + minGap) : (level < bid - minGap);
   if(pendingValid)
     {
      double risk = MathAbs(level - sl);
      double tp   = RoundToTick(isBuy ? level + InpTakeProfitR * risk : level - InpTakeProfitR * risk);
      double lots = CalcLots(isBuy, level, sl);
      if(lots <= 0)
        {
         Log(side + " skipped: calculated size is below the broker minimum lot", true);
         return false;
        }
      bool ok = isBuy ? trade.BuyStop(lots, level, _Symbol, sl, tp, ORDER_TIME_GTC, 0, DayTag(day))
                      : trade.SellStop(lots, level, _Symbol, sl, tp, ORDER_TIME_GTC, 0, DayTag(day));
      g_lastSend = TimeCurrent();
      if(ok && (trade.ResultRetcode() == TRADE_RETCODE_DONE || trade.ResultRetcode() == TRADE_RETCODE_PLACED))
        {
         Log(StringFormat("%s STOP placed #%I64u: %.2f lots @ %s, SL %s, TP %s (range %s - %s)", side, trade.ResultOrder(), lots,
                          DoubleToString(level, _Digits), DoubleToString(sl, _Digits), DoubleToString(tp, _Digits),
                          DoubleToString(lo, _Digits), DoubleToString(hi, _Digits)));
         return true;
        }
      Log(StringFormat("%s STOP failed: %u %s", side, trade.ResultRetcode(), trade.ResultRetcodeDescription()), true);
      return false;
     }

   // price is already beyond the level
   if(InpIfBeyond == BEYOND_WAIT)
     {
      Log(side + " waiting: price is already beyond the breakout level", true);
      return false;
     }
   double entry = isBuy ? ask : bid;
   double risk  = MathAbs(entry - sl);
   if((isBuy && entry <= sl) || (!isBuy && entry >= sl))
     {
      Log(side + " skipped: price is already beyond the stop level", true);
      return false;
     }
   double tp   = RoundToTick(isBuy ? entry + InpTakeProfitR * risk : entry - InpTakeProfitR * risk);
   double lots = CalcLots(isBuy, entry, sl);
   if(lots <= 0)
     {
      Log(side + " skipped: calculated size is below the broker minimum lot", true);
      return false;
     }
   bool ok = isBuy ? trade.Buy(lots, _Symbol, 0, sl, tp, DayTag(day))
                   : trade.Sell(lots, _Symbol, 0, sl, tp, DayTag(day));
   g_lastSend = TimeCurrent();
   if(ok && trade.ResultRetcode() == TRADE_RETCODE_DONE)
     {
      Log(StringFormat("%s at market (price was beyond the level): %.2f lots @ %s, SL %s, TP %s", side, lots,
                       DoubleToString(trade.ResultPrice(), _Digits), DoubleToString(sl, _Digits), DoubleToString(tp, _Digits)));
      return true;
     }
   Log(StringFormat("%s market order failed: %u %s", side, trade.ResultRetcode(), trade.ResultRetcodeDescription()), true);
   return false;
  }

void ManagePosition(const ulong ticket, const int sec)
  {
   if(!PositionSelectByTicket(ticket))
      return;

   if(g_closeSec >= 0 && sec >= g_closeSec)
     {
      if(trade.PositionClose(ticket))
         Log(StringFormat("Closed position #%I64u at close time", ticket));
      else
         Log(StringFormat("Close #%I64u failed: %u %s", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription()), true);
      return;
     }

   if(!InpUseBreakEven)
      return;
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl   = PositionGetDouble(POSITION_SL);
   double tp   = PositionGetDouble(POSITION_TP);
   bool   isBuy = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   if(sl <= 0)
      return;
   // while SL is still on the losing side, it is the original stop, so R can be read from it (restart-safe)
   bool original = isBuy ? sl < open : sl > open;
   if(!original)
      return;
   double r     = MathAbs(open - sl);
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double gain  = isBuy ? price - open : open - price;
   if(gain < InpBETriggerR * r)
      return;
   double newSL  = RoundToTick(isBuy ? open + InpBEOffsetPoints * Pt() : open - InpBEOffsetPoints * Pt());
   double minGap = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * Pt();
   if(isBuy ? newSL > price - minGap : newSL < price + minGap)
      return;
   if(trade.PositionModify(ticket, newSL, tp))
      Log(StringFormat("Break-even: position #%I64u SL moved to %s", ticket, DoubleToString(newSL, _Digits)));
   else
      Log(StringFormat("Break-even modify #%I64u failed: %u %s", ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription()), true);
  }

//+------------------------------------------------------------------+
//| Core logic, called on every tick and on every trade event        |
//+------------------------------------------------------------------+
void Process()
  {
   datetime now = TimeCurrent();
   datetime day = DayStart(now);
   int      sec = (int)(now - day);

   bool inPlaceWindow = sec >= g_rangeEndSec && sec <= g_rangeEndSec + InpMaxPlaceDelayMin * 60;

   DayState st;
   ScanState(day, false, st);
   bool needHistory = st.buyPending || st.sellPending || inPlaceWindow;
   if(needHistory)
      ScanState(day, true, st);

   // 1) OCO: once a trade has been entered today, the other side must go
   if(st.positionOpen || st.enteredToday)
     {
      if(st.buyPending)
         DeletePending(st.buyTicket, "OCO: the other side was filled");
      if(st.sellPending)
         DeletePending(st.sellTicket, "OCO: the other side was filled");
      st.buyPending = st.sellPending = false;
     }

   // 2) manage an open position
   if(st.positionOpen)
      ManagePosition(st.posTicket, sec);

   // 3) cancel untriggered orders at cancel time
   if(g_cancelSec >= 0 && sec >= g_cancelSec)
     {
      if(st.buyPending)
         DeletePending(st.buyTicket, "cancel time");
      if(st.sellPending)
         DeletePending(st.sellTicket, "cancel time");
      return;
     }

   // 4) place orders after the range, inside the placement window, once per day
   if(!inPlaceWindow || !IsTradingDay(day))
      return;
   if(st.positionOpen || st.enteredToday)
      return;
   bool wantLong  = InpDirection != DIR_SHORT;
   bool wantShort = InpDirection != DIR_LONG;
   bool needBuy   = wantLong && !st.buyPlacedToday;
   bool needSell  = wantShort && !st.sellPlacedToday;
   if(!needBuy && !needSell)
      return;
   if(now - g_lastSend < 3)
      return; // give the server a moment so a just-sent order shows up before we look again

   if(InpMaxSpreadPoints > 0)
     {
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > InpMaxSpreadPoints)
        {
         Log(StringFormat("Waiting: spread %d > max %d points", (int)spread, InpMaxSpreadPoints), true);
         return;
        }
     }

   double hi = 0, lo = 0;
   int    bars = 0;
   if(!GetRange(day, hi, lo, bars))
     {
      Log("Waiting: no M1 data for the range yet", true);
      return;
     }
   double sizePts = (hi - lo) / Pt();
   if((InpMinRangePoints > 0 && sizePts < InpMinRangePoints) || (InpMaxRangePoints > 0 && sizePts > InpMaxRangePoints))
     {
      Log(StringFormat("No trade today: range %.0f points is outside the allowed size", sizePts), true);
      return;
     }

   if(needBuy)
      PlaceSide(true, day, hi, lo);
   if(needSell)
      PlaceSide(false, day, hi, lo);
  }

//+------------------------------------------------------------------+
//| Standard handlers                                                |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_rangeStartSec = ParseHHMM(InpRangeStart);
   g_rangeEndSec   = ParseHHMM(InpRangeEnd);
   g_cancelSec     = ParseHHMM(InpCancelTime);
   g_closeSec      = ParseHHMM(InpCloseTime);

   if(g_rangeStartSec < 0 || g_rangeEndSec < 0 || g_rangeEndSec <= g_rangeStartSec)
     {
      Print("ORB: invalid range times. Use HH:MM and make sure the end is after the start.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(g_cancelSec == -2 || g_closeSec == -2)
     {
      Print("ORB: invalid cancel/close time. Use HH:MM or leave empty.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(g_cancelSec >= 0 && g_cancelSec <= g_rangeEndSec)
     {
      Print("ORB: cancel time must be after the range end.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpTakeProfitR <= 0 || InpRiskPercent <= 0 || InpFixedLots <= 0)
     {
      Print("ORB: take profit R, risk % and fixed lots must be positive.");
      return INIT_PARAMETERS_INCORRECT;
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_logFile = StringFormat("ORB_%s_%I64u.log", _Symbol, InpMagic);

   // restart recovery report: everything below is read from the server, nothing is kept in memory
   datetime day = DayStart(TimeCurrent());
   DayState st;
   ScanState(day, true, st);
   Log(StringFormat("Started. Today: buy pending=%s, sell pending=%s, position=%s, entered today=%s",
                    st.buyPending ? "yes" : "no", st.sellPending ? "yes" : "no",
                    st.positionOpen ? "yes" : "no", st.enteredToday ? "yes" : "no"));
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   Process();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   // react to fills immediately so the opposite order is cancelled without waiting for the next tick
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.symbol == _Symbol)
      Process();
  }
//+------------------------------------------------------------------+
