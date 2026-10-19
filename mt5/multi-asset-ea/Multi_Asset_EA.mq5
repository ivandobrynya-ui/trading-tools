//+------------------------------------------------------------------+
//| Multi_Asset_EA.mq5                                               |
//| One Expert Advisor trades a list of symbols from a single chart  |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "Trend + pullback EA for a list of symbols (Forex, metals, commodities) from one chart."
#property description "OnTimer loop over the symbols, lots from risk % and each symbol's tick value and volume"
#property description "step, sessions, daily loss limit, max positions overall and per symbol. State is read"
#property description "from the trade server every cycle, so a restart never duplicates an entry."

#include <Trade\Trade.mqh>

//--- enums
enum ENUM_TRADE_DIR
  {
   DIR_BOTH  = 0,   // Long and short
   DIR_LONG  = 1,   // Long only
   DIR_SHORT = 2    // Short only
  };

enum ENUM_SL_MODE
  {
   SL_ATR    = 0,   // ATR multiple
   SL_POINTS = 1,   // Fixed points
   SL_SWING  = 2    // Beyond the swing high/low of the last N bars
  };

enum ENUM_TP_MODE
  {
   TP_R      = 0,   // R multiple of the stop distance
   TP_ATR    = 1,   // ATR multiple
   TP_POINTS = 2,   // Fixed points
   TP_NONE   = 3    // No take profit
  };

enum ENUM_DL_SCOPE
  {
   DL_EA      = 0,  // This EA only (magic number)
   DL_ACCOUNT = 1   // Whole account
  };

//--- inputs
input group "Symbols and timer"
input string          InpSymbols      = "EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD"; // Symbols, comma separated
input string          InpSymbolSuffix = "";        // Broker suffix added to each symbol (e.g. .m)
input ENUM_TIMEFRAMES InpSignalTF     = PERIOD_H1; // Signal timeframe (current = chart)
input int             InpTimerSeconds = 5;         // Run the symbol loop every N seconds

input group "Trend filter"
input bool            InpUseTrend = true;           // Trade only with the trend
input ENUM_TIMEFRAMES InpTrendTF  = PERIOD_CURRENT; // Trend timeframe (current = signal timeframe)
input int             InpFastEMA  = 50;             // Fast EMA
input int             InpSlowEMA  = 200;            // Slow EMA

input group "Pullback entry (RSI)"
input int            InpRsiPeriod       = 14;       // RSI period
input double         InpRsiBuyLevel     = 40;       // Buy: RSI was below this level and closed back above
input double         InpRsiSellLevel    = 60;       // Sell: RSI was above this level and closed back below
input ENUM_TRADE_DIR InpDirection       = DIR_BOTH; // Direction
input int            InpEntryWindowMin  = 15;       // Enter within N minutes after the signal bar closes (0 = whole bar)
input bool           InpCloseOnOpposite = false;    // Close a position on an opposite signal

input group "Sessions (server time) and days"
input string InpSession1 = "08:00-20:00"; // Session 1, HH:MM-HH:MM (both empty = any time)
input string InpSession2 = "";            // Session 2, HH:MM-HH:MM (empty = off)
input bool   InpMon = true;               // Trade Monday
input bool   InpTue = true;               // Trade Tuesday
input bool   InpWed = true;               // Trade Wednesday
input bool   InpThu = true;               // Trade Thursday
input bool   InpFri = true;               // Trade Friday

input group "Stop loss and take profit"
input int          InpAtrPeriod      = 14;     // ATR period
input ENUM_SL_MODE InpSLMode         = SL_ATR; // Stop loss mode
input double       InpSLAtrMult      = 1.5;    // SL: ATR multiple
input double       InpSLPoints       = 300;    // SL: fixed points
input int          InpSwingBars      = 10;     // SL: swing lookback (bars)
input double       InpSwingBufferAtr = 0.2;    // SL: buffer beyond the swing (ATR multiple)
input ENUM_TP_MODE InpTPMode         = TP_R;   // Take profit mode
input double       InpTPR            = 2.0;    // TP: R multiple
input double       InpTPAtrMult      = 3.0;    // TP: ATR multiple
input double       InpTPPoints       = 600;    // TP: fixed points
input bool         InpUseBreakEven   = false;  // Move SL to break-even
input double       InpBETriggerR     = 1.0;    // Break-even trigger (R multiple)
input double       InpBELockR        = 0.1;    // Break-even lock-in (R multiple)
input bool         InpUseTrailing    = false;  // ATR trailing stop (starts once it locks in profit)
input double       InpTrailAtrMult   = 2.0;    // Trailing distance (ATR multiple)

input group "Risk and capital protection"
input double        InpRiskPercent       = 0.5;   // Risk per trade (% of balance)
input double        InpMaxSpreadAtrPct   = 10;    // Max spread (% of ATR, 0 = off)
input int           InpMaxPositions      = 4;     // Max open positions, all symbols
input int           InpMaxPerSymbol      = 1;     // Max open positions per symbol
input double        InpDailyLossPct      = 3.0;   // Daily loss limit (% of day start balance, 0 = off)
input ENUM_DL_SCOPE InpDailyScope        = DL_EA; // Daily loss counts
input bool          InpCloseOnDailyLimit = true;  // Close positions when the daily limit is hit
input bool          InpDailyRiskBudget   = true;  // Skip a trade if its stop plus all open stops could pass the daily limit

input group "General"
input ulong InpMagic          = 261017; // Magic number
input int   InpSlippagePoints = 20;     // Max slippage (points)
input bool  InpLogToFile      = true;   // Write a decision log and an entries CSV to Common\Files

input group "Testing"
input bool  InpTestStateless  = false;  // Forget all memory every cycle (simulates a restart each cycle)

//--- per-symbol context. Handles are references to terminal indicators; the two
//--- datetimes are only a cache: wiping them never changes a trading decision.
struct SymbolCtx
  {
   string   name;
   int      hFast;
   int      hSlow;
   int      hRsi;
   int      hAtr;
   datetime doneBar;   // bar whose signal has a final outcome (entered, no signal, window passed)
   datetime lastSend;  // last order send, so the server can catch up before the next look
   string   lastLog;   // log de-duplication only
   bool     specDone;  // symbol specification already written to the log
  };

//--- globals
CTrade          trade;
SymbolCtx       g_sym[];
int             g_count   = 0;
ENUM_TIMEFRAMES g_tf      = PERIOD_H1;
ENUM_TIMEFRAMES g_trendTF = PERIOD_H1;
int             g_s1From = -1, g_s1To = -1, g_s2From = -1, g_s2To = -1;
bool            g_hedging = true;
string          g_logFile = "";
string          g_csvFile = "";
string          g_haltVar = "";
string          g_lastLog = "";

//--- the book: everything below is rebuilt from the trade server at the start of every cycle
int      g_total = 0;        // our open positions, all symbols
int      g_buys[];           // our open buys per symbol
int      g_sells[];          // our open sells per symbol
datetime g_lastEntry[];      // time of our last entry deal per symbol
double   g_floating = 0;     // floating P/L (scope of the daily limit)
double   g_realized = 0;     // closed P/L today (scope of the daily limit)
double   g_dayStartBalance = 0;
double   g_worstCase = 0;    // day P/L if every open stop is hit now (closed P/L + losses at our stops)
bool     g_noStop    = false; // one of our positions has no stop loss, so the worst case is unknown

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
int ParseHHMM(const string s)
  {
   string parts[];
   if(StringSplit(s, ':', parts) != 2)
      return -1;
   int h = (int)StringToInteger(parts[0]);
   int m = (int)StringToInteger(parts[1]);
   if(h < 0 || h > 24 || m < 0 || m > 59 || h * 60 + m > 1440)
      return -1;
   return h * 3600 + m * 60;
  }

// "HH:MM-HH:MM" -> seconds of day. Empty -> from = -1 (off). Returns false on a bad format.
bool ParseSession(const string text, int &from, int &to)
  {
   from = -1;
   to   = -1;
   string s = text;
   StringTrimLeft(s);
   StringTrimRight(s);
   if(s == "")
      return true;
   string parts[];
   if(StringSplit(s, '-', parts) != 2)
      return false;
   StringTrimLeft(parts[0]);
   StringTrimRight(parts[0]);
   StringTrimLeft(parts[1]);
   StringTrimRight(parts[1]);
   from = ParseHHMM(parts[0]);
   to   = ParseHHMM(parts[1]);
   return from >= 0 && to >= 0 && from != to;
  }

bool InWindow(const int sec, const int from, const int to)
  {
   if(from < 0)
      return false;
   if(from < to)
      return sec >= from && sec < to;
   return sec >= from || sec < to; // overnight session, e.g. 22:00-02:00
  }

bool InSession(const datetime now)
  {
   if(g_s1From < 0 && g_s2From < 0)
      return true;
   int sec = (int)(now % 86400);
   return InWindow(sec, g_s1From, g_s1To) || InWindow(sec, g_s2From, g_s2To);
  }

bool IsTradingDay(const datetime now)
  {
   MqlDateTime dt;
   TimeToStruct(now, dt);
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

void WriteLine(const string file, const string line)
  {
   int h = FileOpen(file, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

void Log(const string msg)
  {
   string line = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + " " + msg;
   Print(line);
   if(InpLogToFile)
      WriteLine(g_logFile, line);
  }

void LogOnce(const string msg)
  {
   if(msg == g_lastLog)
      return;
   g_lastLog = msg;
   Log(msg);
  }

// Writes a symbol message once per key, so a wait repeated every cycle is logged one time
void LogSym(const int i, const string msg, const string key = "")
  {
   string k = key == "" ? msg : key;
   if(k == g_sym[i].lastLog)
      return;
   g_sym[i].lastLog = k;
   Log("[" + g_sym[i].name + "] " + msg);
  }

int SymbolIndex(const string name)
  {
   for(int i = 0; i < g_count; i++)
      if(g_sym[i].name == name)
         return i;
   return -1;
  }

double RoundToTick(const string sym, const double price)
  {
   double tick = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0)
      tick = SymbolInfoDouble(sym, SYMBOL_POINT);
   return NormalizeDouble(MathRound(price / tick) * tick, (int)SymbolInfoInteger(sym, SYMBOL_DIGITS));
  }

// Floors to the volume step. Below the broker minimum -> 0 (skip), never rounded up to more risk.
double NormalizeLots(const string sym, const double lots)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(step <= 0)
      step = 0.01;
   double v = MathFloor(lots / step + 1e-9) * step;
   if(v < vmin)
      return 0.0;
   return NormalizeDouble(MathMin(v, vmax), 8);
  }

//+------------------------------------------------------------------+
//| Loss of 1 lot if price goes from entry to sl, in account money.  |
//| Two independent ways: the symbol's tick value, and the terminal's|
//| own profit calculator. Brokers sometimes report a wrong tick     |
//| value (on MetaQuotes-Demo metals in the tester it is 10x too     |
//| small), so the larger loss is used: the lot can only get smaller.|
//+------------------------------------------------------------------+
double LossPerLot(const string sym, const bool isBuy, const double entry, const double sl, double &lossTick, double &lossCalc)
  {
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0)
      tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   lossTick = (tickSize > 0 && tickValue > 0) ? MathAbs(entry - sl) / tickSize * tickValue : 0;
   lossCalc = 0;
   if(OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, 1.0, entry, sl, lossCalc))
      lossCalc = MathAbs(lossCalc);
   else
      lossCalc = 0;
   return MathMax(lossTick, lossCalc);
  }

// Loss (negative) of a position if its stop is hit. A stop at break-even or better counts as 0.
double PnlAtStop(const string sym, const bool isBuy, const double volume, const double open, const double sl)
  {
   if(isBuy ? sl >= open : sl <= open)
      return 0;
   double lossTick = 0, lossCalc = 0;
   return -LossPerLot(sym, isBuy, open, sl, lossTick, lossCalc) * volume;
  }

// One line per symbol with its trading specification and the tick value cross-check
void LogSpec(const int i)
  {
   string sym = g_sym[i].name;
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double a[];
   if(bid <= 0 || CopyBuffer(g_sym[i].hAtr, 0, 1, 1, a) != 1 || a[0] <= 0)
      return;
   g_sym[i].specDone = true;
   double lossTick = 0, lossCalc = 0;
   LossPerLot(sym, true, bid, bid - a[0], lossTick, lossCalc);
   double ratio = (lossTick > 0 && lossCalc > 0) ? lossCalc / lossTick : 0;
   double margin = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, sym, 1.0, SymbolInfoDouble(sym, SYMBOL_ASK), margin))
      margin = 0;
   Log(StringFormat("[%s] spec: contract %s, tick size %s, tick value %s %s, volume %s..%s step %s, margin for 1 lot %.2f. "
                    "1 lot over 1 ATR: loss %.2f by tick value, %.2f by the terminal calculator%s",
                    sym, DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 2),
                    DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE), (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)),
                    DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), 6), AccountInfoString(ACCOUNT_CURRENCY),
                    DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), 2), DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), 2),
                    DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), 2), margin, lossTick, lossCalc,
                    (ratio > 0 && MathAbs(ratio - 1.0) > 0.05) ? StringFormat(". WARNING: they differ %.2fx, lots use the larger loss", ratio) : ""));
  }

//+------------------------------------------------------------------+
//| Read our positions, today's deals and the day's P/L from server  |
//+------------------------------------------------------------------+
bool ReadBook(const datetime now, const datetime day)
  {
   g_total    = 0;
   g_floating = 0;
   g_realized = 0;
   g_noStop   = false;
   double stopPL = 0, otherFloating = 0;
   ArrayInitialize(g_buys, 0);
   ArrayInitialize(g_sells, 0);
   ArrayInitialize(g_lastEntry, 0);

   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(ticket == 0)
         continue;
      bool   mine = (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic;
      double pl   = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(mine || InpDailyScope == DL_ACCOUNT)
         g_floating += pl;
      if(!mine)
        {
         otherFloating += pl;
         continue;
        }
      g_total++; // a position of ours on a symbol no longer in the list still counts toward the total
      double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0)
         g_noStop = true;
      else
         stopPL += PnlAtStop(PositionGetString(POSITION_SYMBOL), (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY,
                             PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_OPEN), sl) + PositionGetDouble(POSITION_SWAP);
      int i = SymbolIndex(PositionGetString(POSITION_SYMBOL));
      if(i < 0)
         continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         g_buys[i]++;
      else
         g_sells[i]++;
     }

   // market orders still in flight count as positions
   for(int o = OrdersTotal() - 1; o >= 0; o--)
     {
      ulong ticket = OrderGetTicket(o);
      if(ticket == 0 || (ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic)
         continue;
      ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(type != ORDER_TYPE_BUY && type != ORDER_TYPE_SELL)
         continue;
      g_total++;
      int i = SymbolIndex(OrderGetString(ORDER_SYMBOL));
      if(i < 0)
         continue;
      if(type == ORDER_TYPE_BUY)
         g_buys[i]++;
      else
         g_sells[i]++;
      g_lastEntry[i] = now;
     }

   // history from the start of the day, or from the oldest current bar if that is earlier
   datetime from = day;
   for(int i = 0; i < g_count; i++)
     {
      datetime t0 = iTime(g_sym[i].name, g_tf, 0);
      if(t0 > 0 && t0 < from)
         from = t0;
     }
   if(!HistorySelect(from, now + 86400))
      return false;

   double accountToday = 0;
   int    deals = HistoryDealsTotal();
   for(int d = 0; d < deals; d++)
     {
      ulong ticket = HistoryDealGetTicket(d);
      if(ticket == 0)
         continue;
      ENUM_DEAL_TYPE type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue; // deposits and other balance operations are not trading P/L
      datetime t    = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      bool     mine = (ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC) == InpMagic;
      if(t >= day)
        {
         double pl = HistoryDealGetDouble(ticket, DEAL_PROFIT) + HistoryDealGetDouble(ticket, DEAL_SWAP) +
                     HistoryDealGetDouble(ticket, DEAL_COMMISSION) + HistoryDealGetDouble(ticket, DEAL_FEE);
         accountToday += pl;
         if(mine || InpDailyScope == DL_ACCOUNT)
            g_realized += pl;
        }
      if(!mine)
         continue;
      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_IN && entry != DEAL_ENTRY_INOUT)
         continue;
      int i = SymbolIndex(HistoryDealGetString(ticket, DEAL_SYMBOL));
      if(i >= 0 && t > g_lastEntry[i])
         g_lastEntry[i] = t;
     }
   g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE) - accountToday;
   g_worstCase       = g_realized + stopPL + (InpDailyScope == DL_ACCOUNT ? otherFloating : 0);
   return true;
  }

//+------------------------------------------------------------------+
//| Closing                                                          |
//+------------------------------------------------------------------+
bool ClosePosition(const ulong ticket, const string sym, const string reason)
  {
   trade.SetTypeFillingBySymbol(sym);
   if(trade.PositionClose(ticket))
     {
      Log(StringFormat("[%s] closed #%I64u (%s)", sym, ticket, reason));
      return true;
     }
   LogOnce(StringFormat("[%s] close #%I64u failed (%s): %u %s", sym, ticket, reason, trade.ResultRetcode(), trade.ResultRetcodeDescription()));
   return false;
  }

void CloseAll(const string reason)
  {
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(ticket == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ClosePosition(ticket, PositionGetString(POSITION_SYMBOL), reason);
     }
  }

// Closes our positions of one symbol and one direction. Returns how many were closed.
int CloseSide(const int i, const ENUM_POSITION_TYPE side, const string reason)
  {
   int closed = 0;
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(ticket == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != g_sym[i].name || (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != side)
         continue;
      if(ClosePosition(ticket, g_sym[i].name, reason))
         closed++;
     }
   return closed;
  }

//+------------------------------------------------------------------+
//| Daily loss limit: closed P/L today + floating P/L. The halt flag |
//| lives in a terminal global variable, flushed to disk at once,    |
//| so a restart keeps the EA halted until the next server day.      |
//+------------------------------------------------------------------+
bool CheckDailyLimit(const datetime day)
  {
   if(InpDailyLossPct <= 0)
      return false;
   double limit   = g_dayStartBalance * InpDailyLossPct / 100.0;
   double pnl     = g_realized + g_floating;
   bool   flagged = GlobalVariableCheck(g_haltVar) && (datetime)(long)GlobalVariableGet(g_haltVar) == day;
   if(!flagged && pnl <= -limit)
     {
      GlobalVariableSet(g_haltVar, (double)(long)day);
      GlobalVariablesFlush();
      Log(StringFormat("DAILY LIMIT hit: day P/L %.2f (closed %.2f, floating %.2f) <= -%.2f = %.2f%% of day start balance %.2f. "
                       "Open positions: %d. No new entries until the next server day.",
                       pnl, g_realized, g_floating, limit, InpDailyLossPct, g_dayStartBalance, g_total));
      flagged = true;
     }
   if(flagged && InpCloseOnDailyLimit && g_total > 0)
      CloseAll("daily loss limit");
   return flagged;
  }

//+------------------------------------------------------------------+
//| Signal: closed bars only. Bar 1 = signal bar, bar 2 = the one    |
//| before it. +1 buy, -1 sell, 0 none, -9 indicator data not ready. |
//+------------------------------------------------------------------+
int GetSignal(const int i, double &atr)
  {
   double rsi[], a[], fast[], slow[];
   if(CopyBuffer(g_sym[i].hRsi, 0, 1, 2, rsi) != 2 || CopyBuffer(g_sym[i].hAtr, 0, 1, 1, a) != 1)
      return -9;
   atr = a[0];
   if(atr <= 0)
      return -9;

   bool trendUp = true, trendDown = true;
   if(InpUseTrend)
     {
      if(CopyBuffer(g_sym[i].hFast, 0, 1, 1, fast) != 1 || CopyBuffer(g_sym[i].hSlow, 0, 1, 1, slow) != 1)
         return -9;
      double c = iClose(g_sym[i].name, g_trendTF, 1); // last closed bar of the trend timeframe
      if(c <= 0)
         return -9;
      trendUp   = c > slow[0] && fast[0] > slow[0];
      trendDown = c < slow[0] && fast[0] < slow[0];
     }
   // rsi[0] = bar 2 (older), rsi[1] = bar 1 (signal bar)
   if(InpDirection != DIR_SHORT && trendUp && rsi[0] < InpRsiBuyLevel && rsi[1] >= InpRsiBuyLevel)
      return 1;
   if(InpDirection != DIR_LONG && trendDown && rsi[0] > InpRsiSellLevel && rsi[1] <= InpRsiSellLevel)
      return -1;
   return 0;
  }

//+------------------------------------------------------------------+
//| Entry                                                            |
//+------------------------------------------------------------------+
bool OpenTrade(const int i, const int sig, const double atr, const string tag)
  {
   string sym   = g_sym[i].name;
   bool   isBuy = sig > 0;
   string side  = isBuy ? "BUY" : "SELL";
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
   if(ask <= 0 || bid <= 0)
      return false;

   double spread = ask - bid;
   if(InpMaxSpreadAtrPct > 0 && spread > atr * InpMaxSpreadAtrPct / 100.0)
     {
      LogSym(i, StringFormat("%s signal %s waiting: spread %s is above %.0f%% of ATR %s", side, tag,
                             DoubleToString(spread, digits), InpMaxSpreadAtrPct, DoubleToString(atr, digits)), tag + " spread");
      return false;
     }

   //--- stop loss
   double entry = isBuy ? ask : bid;
   double sl    = 0;
   switch(InpSLMode)
     {
      case SL_ATR:
         sl = isBuy ? entry - InpSLAtrMult * atr : entry + InpSLAtrMult * atr;
         break;
      case SL_POINTS:
         sl = isBuy ? entry - InpSLPoints * point : entry + InpSLPoints * point;
         break;
      case SL_SWING:
        {
         int k = isBuy ? iLowest(sym, g_tf, MODE_LOW, InpSwingBars, 1) : iHighest(sym, g_tf, MODE_HIGH, InpSwingBars, 1);
         if(k < 0)
            return false;
         double swing = isBuy ? iLow(sym, g_tf, k) : iHigh(sym, g_tf, k);
         sl = isBuy ? swing - InpSwingBufferAtr * atr : swing + InpSwingBufferAtr * atr;
         break;
        }
     }
   sl = RoundToTick(sym, sl);
   double risk   = isBuy ? entry - sl : sl - entry;
   double minGap = (double)MathMax(SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL), SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL)) * point + spread;
   if(risk <= minGap)
     {
      LogSym(i, StringFormat("%s signal %s waiting: stop %s is too close to the price %s", side, tag,
                             DoubleToString(sl, digits), DoubleToString(entry, digits)), tag + " stop");
      return false;
     }

   //--- take profit
   double tp = 0;
   switch(InpTPMode)
     {
      case TP_R:      tp = isBuy ? entry + InpTPR * risk : entry - InpTPR * risk; break;
      case TP_ATR:    tp = isBuy ? entry + InpTPAtrMult * atr : entry - InpTPAtrMult * atr; break;
      case TP_POINTS: tp = isBuy ? entry + InpTPPoints * point : entry - InpTPPoints * point; break;
      case TP_NONE:   tp = 0; break;
     }
   if(tp != 0)
      tp = RoundToTick(sym, tp);

   //--- position size: risk money / loss of 1 lot at the stop, floored to the volume step
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney  = balance * InpRiskPercent / 100.0;
   double tickSize   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   double lossTick   = 0, lossCalc = 0;
   double lossPerLot = LossPerLot(sym, isBuy, entry, sl, lossTick, lossCalc);
   if(lossPerLot <= 0)
     {
      LogSym(i, side + " signal " + tag + " waiting: tick value is not available yet");
      return false;
     }
   double rawLots = riskMoney / lossPerLot;
   double lots    = NormalizeLots(sym, rawLots);
   double step    = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin    = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   if(lots <= 0)
     {
      LogSym(i, StringFormat("%s signal %s skipped: risk %.2f allows %.4f lots, below the minimum %.2f", side, tag, riskMoney, rawLots, vmin), tag + " minlot");
      return false;
     }
   double margin = 0;
   if(OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, lots, entry, margin) && margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      LogSym(i, StringFormat("%s signal %s waiting: margin %.2f is above free margin %.2f", side, tag, margin, AccountInfoDouble(ACCOUNT_MARGIN_FREE)), tag + " margin");
      return false;
     }

   //--- daily risk budget: this stop plus every open stop must not be able to take the day past the limit
   double newRisk = lots * lossPerLot;
   if(InpDailyRiskBudget && InpDailyLossPct > 0)
     {
      double limit = g_dayStartBalance * InpDailyLossPct / 100.0;
      if(g_noStop)
        {
         LogSym(i, side + " signal " + tag + " waiting: an open position has no stop loss, the daily risk is unknown", tag + " nostop");
         return false;
        }
      if(g_worstCase - newRisk < -limit)
        {
         LogSym(i, StringFormat("%s signal %s waiting: daily risk budget. Closed today plus open stops %.2f, this stop -%.2f, limit -%.2f",
                                side, tag, g_worstCase, newRisk, limit), tag + " budget");
         return false;
        }
     }

   //--- send
   trade.SetTypeFillingBySymbol(sym);
   string comment = "MAEA " + tag;
   bool   ok = isBuy ? trade.Buy(lots, sym, 0, sl, tp, comment) : trade.Sell(lots, sym, 0, sl, tp, comment);
   g_sym[i].lastSend = TimeCurrent();
   uint rc = trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_DONE_PARTIAL && rc != TRADE_RETCODE_PLACED))
     {
      LogSym(i, StringFormat("%s signal %s order failed: %u %s", side, tag, rc, trade.ResultRetcodeDescription()), tag + " fail " + (string)rc);
      return false;
     }

   g_sym[i].doneBar = iTime(sym, g_tf, 0);
   g_lastEntry[i]   = TimeCurrent();
   g_worstCase     -= newRisk;
   g_total++;
   if(isBuy)
      g_buys[i]++;
   else
      g_sells[i]++;

   double price = trade.ResultPrice() > 0 ? trade.ResultPrice() : entry;
   Log(StringFormat("[%s] %s %s lots @ %s, SL %s, TP %s, signal bar %s | risk %.2f = %.2f%% of %.2f, loss per lot at SL %.2f, deal #%I64u",
                    sym, side, DoubleToString(lots, 2), DoubleToString(price, digits), DoubleToString(sl, digits), DoubleToString(tp, digits),
                    tag, riskMoney, InpRiskPercent, balance, lossPerLot, trade.ResultDeal()));
   if(InpLogToFile)
      WriteLine(g_csvFile, StringFormat("%s,%s,%s,%s,%s,%s,%s,%s,%s,%.2f,%.2f,%.6f,%s,%.4f,%.4f,%s,%s,%.6f,%s,%I64u",
                                        TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), sym, tag, side,
                                        DoubleToString(price, digits), DoubleToString(sl, digits), DoubleToString(tp, digits),
                                        DoubleToString(atr, digits), DoubleToString(spread, digits), balance, riskMoney, tickValue,
                                        DoubleToString(tickSize, digits), lossTick, lossCalc, DoubleToString(step, 3),
                                        DoubleToString(vmin, 3), rawLots, DoubleToString(lots, 3), trade.ResultDeal()));
   return true;
  }

//+------------------------------------------------------------------+
//| One symbol, one cycle. Every check reads server state, so the    |
//| answer after a restart is the same as without one.               |
//+------------------------------------------------------------------+
void ProcessSymbol(const int i, const datetime now, const bool halted)
  {
   string   sym = g_sym[i].name;
   datetime t0  = iTime(sym, g_tf, 0);
   if(t0 <= 0 || now >= t0 + PeriodSeconds(g_tf))
      return; // no tick of this symbol in the new bar yet
   if(g_sym[i].doneBar == t0)
      return; // cache: this bar already has a final outcome

   if(g_lastEntry[i] >= t0)
     {
      g_sym[i].doneBar = t0; // we already entered on this bar: one entry per signal
      return;
     }

   double atr = 0;
   int    sig = GetSignal(i, atr);
   if(sig == -9)
     {
      LogSym(i, "waiting for indicator data");
      return;
     }
   if(sig == 0)
     {
      g_sym[i].doneBar = t0;
      return;
     }

   string tag  = TimeToString(iTime(sym, g_tf, 1), TIME_DATE | TIME_MINUTES);
   string side = sig > 0 ? "BUY" : "SELL";
   if(InpEntryWindowMin > 0 && now - t0 > InpEntryWindowMin * 60)
     {
      LogSym(i, side + " signal " + tag + " skipped: entry window passed");
      g_sym[i].doneBar = t0;
      return;
     }
   if(halted)
     {
      LogSym(i, side + " signal " + tag + " skipped: daily loss limit reached");
      return;
     }
   if(!IsTradingDay(now) || !InSession(now))
     {
      LogSym(i, side + " signal " + tag + " waiting: outside trading days or sessions");
      return;
     }
   if(now - g_sym[i].lastSend < 3)
      return;

   int opposite = sig > 0 ? g_sells[i] : g_buys[i];
   if(opposite > 0 && InpCloseOnOpposite)
     {
      int closed = CloseSide(i, sig > 0 ? POSITION_TYPE_SELL : POSITION_TYPE_BUY, "opposite signal " + tag);
      opposite -= closed;
      g_total  -= closed;
      if(sig > 0)
         g_sells[i] -= closed;
      else
         g_buys[i] -= closed;
     }
   if(opposite > 0 && !g_hedging)
     {
      LogSym(i, side + " signal " + tag + " waiting: an opposite position is open (netting account)");
      return;
     }
   int maxPer = g_hedging ? InpMaxPerSymbol : 1;
   if(g_buys[i] + g_sells[i] >= maxPer)
     {
      LogSym(i, side + " signal " + tag + " waiting: max positions for this symbol");
      return;
     }
   if(g_total >= InpMaxPositions)
     {
      LogSym(i, side + " signal " + tag + " waiting: max positions overall");
      return;
     }
   OpenTrade(i, sig, atr, tag);
  }

//+------------------------------------------------------------------+
//| Break-even and trailing. While the SL is still on the losing     |
//| side it is the original stop, so R is read from it: no memory.   |
//+------------------------------------------------------------------+
void ManagePositions()
  {
   if(!InpUseBreakEven && !InpUseTrailing)
      return;
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(ticket == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      int    i   = SymbolIndex(sym);
      if(i < 0)
         continue;
      bool   isBuy = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double open  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      double price = isBuy ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      double newSL = sl;
      string why   = "";

      if(InpUseBreakEven && sl > 0 && (isBuy ? sl < open : sl > open))
        {
         double r    = MathAbs(open - sl);
         double gain = isBuy ? price - open : open - price;
         if(gain >= InpBETriggerR * r)
           {
            newSL = isBuy ? open + InpBELockR * r : open - InpBELockR * r;
            why   = "break-even";
           }
        }
      if(InpUseTrailing)
        {
         double a[];
         if(CopyBuffer(g_sym[i].hAtr, 0, 1, 1, a) == 1 && a[0] > 0)
           {
            double trail  = isBuy ? price - InpTrailAtrMult * a[0] : price + InpTrailAtrMult * a[0];
            bool   locks  = isBuy ? trail > open : trail < open;
            bool   better = isBuy ? trail > newSL : (newSL == 0 || trail < newSL);
            if(locks && better)
              {
               newSL = trail;
               why   = "trailing";
              }
           }
        }
      if(why == "")
         continue;
      newSL = RoundToTick(sym, newSL);
      double tick = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      bool improves = isBuy ? newSL > sl + tick / 2 : (sl == 0 || newSL < sl - tick / 2);
      if(!improves)
         continue;
      double minGap = (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;
      if(isBuy ? newSL > price - minGap : newSL < price + minGap)
         continue;
      if(trade.PositionModify(ticket, newSL, tp))
         Log(StringFormat("[%s] %s: #%I64u SL %s -> %s", sym, why, ticket, DoubleToString(sl, (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)),
                          DoubleToString(newSL, (int)SymbolInfoInteger(sym, SYMBOL_DIGITS))));
      else
         LogSym(i, StringFormat("%s modify #%I64u failed: %u %s", why, ticket, trade.ResultRetcode(), trade.ResultRetcodeDescription()), why + " fail " + (string)ticket);
     }
  }

//+------------------------------------------------------------------+
//| The cycle                                                        |
//+------------------------------------------------------------------+
void ForgetMemory()
  {
   for(int i = 0; i < g_count; i++)
     {
      g_sym[i].doneBar  = 0;
      g_sym[i].lastSend = 0;
     }
  }

void Cycle()
  {
   if(InpTestStateless)
      ForgetMemory();
   datetime now = TimeCurrent();
   if(now <= 0)
      return;
   datetime day = now - now % 86400;
   if(!ReadBook(now, day))
      return;
   bool halted = CheckDailyLimit(day);
   ManagePositions();
   for(int i = 0; i < g_count; i++)
     {
      if(!g_sym[i].specDone)
         LogSpec(i);
      ProcessSymbol(i, now, halted);
     }
  }

//+------------------------------------------------------------------+
//| Standard handlers                                                |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_tf      = InpSignalTF == PERIOD_CURRENT ? (ENUM_TIMEFRAMES)_Period : InpSignalTF;
   g_trendTF = InpTrendTF == PERIOD_CURRENT ? g_tf : InpTrendTF;

   if(!ParseSession(InpSession1, g_s1From, g_s1To) || !ParseSession(InpSession2, g_s2From, g_s2To))
     {
      Print("MAEA: invalid session. Use HH:MM-HH:MM, for example 08:00-20:00, or leave it empty.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpRiskPercent <= 0 || InpMaxPositions < 1 || InpMaxPerSymbol < 1 || InpTimerSeconds < 1)
     {
      Print("MAEA: risk %, max positions and timer seconds must be positive.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpFastEMA < 1 || InpSlowEMA <= InpFastEMA || InpRsiPeriod < 2 || InpAtrPeriod < 1 || InpRsiBuyLevel >= InpRsiSellLevel)
     {
      Print("MAEA: check the indicator settings (fast EMA < slow EMA, RSI buy level < sell level).");
      return INIT_PARAMETERS_INCORRECT;
     }
   if((InpSLMode == SL_ATR && InpSLAtrMult <= 0) || (InpSLMode == SL_POINTS && InpSLPoints <= 0) ||
      (InpSLMode == SL_SWING && InpSwingBars < 1) || (InpTPMode == TP_R && InpTPR <= 0) ||
      (InpTPMode == TP_ATR && InpTPAtrMult <= 0) || (InpTPMode == TP_POINTS && InpTPPoints <= 0))
     {
      Print("MAEA: stop loss and take profit values must be positive.");
      return INIT_PARAMETERS_INCORRECT;
     }

   g_hedging = AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
   g_logFile = StringFormat("MAEA_%I64u.log", InpMagic);
   g_csvFile = StringFormat("MAEA_%I64u_entries.csv", InpMagic);
   g_haltVar = StringFormat("MAEA_%I64u_halt", InpMagic);
   if(InpLogToFile)
     {
      if(MQLInfoInteger(MQL_TESTER))
        {
         FileDelete(g_logFile, FILE_COMMON);
         FileDelete(g_csvFile, FILE_COMMON);
        }
      if(!FileIsExist(g_csvFile, FILE_COMMON))
         WriteLine(g_csvFile, "time,symbol,signal_bar,side,price,sl,tp,atr,spread,balance,risk_money,tick_value,tick_size,"
                              "loss_per_lot,loss_per_lot_calc,vol_step,vol_min,raw_lots,lots,deal");
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   //--- symbols: skip the ones this broker does not have, keep the rest
   string parts[];
   int    n = StringSplit(InpSymbols, ',', parts);
   g_count = 0;
   ArrayResize(g_sym, 0);
   for(int k = 0; k < n; k++)
     {
      string name = parts[k];
      StringTrimLeft(name);
      StringTrimRight(name);
      if(name == "")
         continue;
      name += InpSymbolSuffix;
      if(SymbolIndex(name) >= 0)
         continue;
      bool custom = false;
      if(!SymbolExist(name, custom) || !SymbolSelect(name, true))
        {
         Log("Symbol not found at this broker, skipped: " + name);
         continue;
        }
      int hFast = iMA(name, g_trendTF, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
      int hSlow = iMA(name, g_trendTF, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
      int hRsi  = iRSI(name, g_tf, InpRsiPeriod, PRICE_CLOSE);
      int hAtr  = iATR(name, g_tf, InpAtrPeriod);
      if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hRsi == INVALID_HANDLE || hAtr == INVALID_HANDLE)
        {
         Log("Indicators could not be created, skipped: " + name);
         continue;
        }
      ArrayResize(g_sym, g_count + 1);
      g_sym[g_count].name     = name;
      g_sym[g_count].hFast    = hFast;
      g_sym[g_count].hSlow    = hSlow;
      g_sym[g_count].hRsi     = hRsi;
      g_sym[g_count].hAtr     = hAtr;
      g_sym[g_count].doneBar  = 0;
      g_sym[g_count].lastSend = 0;
      g_sym[g_count].lastLog  = "";
      g_sym[g_count].specDone = false;
      g_count++;
     }
   if(g_count == 0)
     {
      Print("MAEA: none of the symbols are available.");
      return INIT_PARAMETERS_INCORRECT;
     }
   ArrayResize(g_buys, g_count);
   ArrayResize(g_sells, g_count);
   ArrayResize(g_lastEntry, g_count);

   //--- restart report: everything here comes from the trade server, nothing from memory
   datetime now = TimeCurrent();
   datetime day = now - now % 86400;
   if(ReadBook(now, day))
     {
      bool halted = InpDailyLossPct > 0 && GlobalVariableCheck(g_haltVar) && (datetime)(long)GlobalVariableGet(g_haltVar) == day;
      Log(StringFormat("Started: %d symbols, signal %s, trend %s, %s account, risk %.2f%%, daily limit %.2f%% (risk budget %s)%s. "
                       "Open positions: %d. Today: closed P/L %.2f, floating %.2f, day start balance %.2f, daily limit %s.",
                       g_count, EnumToString(g_tf), EnumToString(g_trendTF), g_hedging ? "hedging" : "netting", InpRiskPercent,
                       InpDailyLossPct, InpDailyRiskBudget ? "on" : "off", InpTestStateless ? ", TEST: stateless" : "", g_total,
                       g_realized, g_floating, g_dayStartBalance, halted ? "HIT (no new entries today)" : "not hit"));
      for(int i = 0; i < g_count; i++)
        {
         datetime t0 = iTime(g_sym[i].name, g_tf, 0);
         Log(StringFormat("[%s] open: %d buy, %d sell; entered on the current bar: %s", g_sym[i].name, g_buys[i], g_sells[i],
                          (t0 > 0 && g_lastEntry[i] >= t0) ? "yes" : "no"));
        }
     }
   EventSetTimer(InpTimerSeconds);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   for(int i = 0; i < g_count; i++)
     {
      IndicatorRelease(g_sym[i].hFast);
      IndicatorRelease(g_sym[i].hSlow);
      IndicatorRelease(g_sym[i].hRsi);
      IndicatorRelease(g_sym[i].hAtr);
     }
   Log(StringFormat("Stopped, reason code %d", reason));
  }

void OnTimer()
  {
   Cycle();
  }

void OnTick()
  {
   // all work runs in OnTimer, so every symbol is handled on the same schedule,
   // not only when the chart symbol gets a tick
  }
//+------------------------------------------------------------------+
