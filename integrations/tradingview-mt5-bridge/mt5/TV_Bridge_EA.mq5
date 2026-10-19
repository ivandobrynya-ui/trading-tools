//+------------------------------------------------------------------+
//| TV_Bridge_EA.mq5                                                 |
//| TradingView -> MetaTrader 5 bridge: executor                     |
//| Reads signal files written by tvb_receiver.py into               |
//| Common\Files\tvb_inbox and trades them with risk-based size,     |
//| SL/TP, duplicate protection, daily loss limit, max drawdown halt |
//| and an emergency stop (chart button, remote kill file, signal).  |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "TradingView alerts -> MT5 orders. Risk-based lots, SL/TP, duplicate protection,"
#property description "daily loss limit, max drawdown halt and an emergency stop."

#include <Trade\Trade.mqh>
#include <Generic\HashSet.mqh>

enum ENUM_SIZE_MODE
  {
   SIZE_RISK  = 0, // Risk % of equity (needs a stop loss)
   SIZE_FIXED = 1  // Fixed lots
  };

input group "Signals"
input string InpInbox        = "tvb_inbox";     // Inbox folder in Common\Files
input int    InpPollMs       = 500;             // Poll interval (ms)
input int    InpMaxAgeSec    = 60;              // Ignore signals older than N seconds (live)
input string InpSymbolMap    = "";              // Symbol map, e.g. "XAUUSD=GOLD,BTCUSDT=BTCUSD"
input string InpSymbolSuffix = "";              // Suffix for broker symbols, e.g. ".m"
input string InpAllowed      = "";              // Allowed broker symbols, comma separated (empty = any)
input bool   InpReplay       = false;           // Replay mode for the Strategy Tester (Common\Files\tvb_replay)

input group "Position size"
input ENUM_SIZE_MODE InpSizeMode       = SIZE_RISK; // Size mode
input double         InpRiskPercent    = 0.5;       // Default risk per trade (% of equity)
input double         InpMaxRiskPercent = 2.0;       // Max risk a signal may request (%)
input double         InpFixedLots      = 0.01;      // Fixed lots
input double         InpMaxLots        = 1.0;       // Max lots per order
input double         InpDefaultSLPts   = 0;         // SL in points if the signal has none (0 = reject)
input double         InpDefaultTPR     = 0;         // TP as R multiple if the signal has none (0 = no TP)

input group "Execution"
input int   InpMaxSpreadPts = 0;      // Max spread in points (0 = off)
input int   InpSlippagePts  = 30;     // Max slippage (points)
input int   InpMaxPositions = 1;      // Max positions per symbol and direction
input bool  InpCloseOpposite = true;  // Close opposite positions before a new entry
input ulong InpMagic        = 777001; // Magic number

input group "Protection"
input double InpDailyLossPct  = 3.0;   // Daily loss limit, % of day-start equity (0 = off)
input bool   InpCloseOnDaily  = true;  // Close all bridge positions when the daily limit is hit
input double InpMaxDDPct      = 10.0;  // Max drawdown from equity peak, % (0 = off) -> halt
input string InpKillFile      = "tvb_KILL.flag"; // Remote kill switch file in Common\Files
input bool   InpResetHalt     = false; // Clear a halt on start (set back to false afterwards)

//--- signal
struct Signal
  {
   string   id;
   string   hash;
   string   symbol;
   string   action;
   string   comment;
   double   sl, tp, slPts, tpPts, risk, lots;
   double   received;  // unix seconds, UTC (live)
   datetime time;      // server time (replay)
   string   file;
   bool     done;
  };

CTrade          trade;
CHashSet<string> gProcessed;
Signal          gReplay[];
string          gMapFrom[], gMapTo[], gAllowed[];
string          gGV;
string          gJournal;
string          gProcessedFile;
string          gLastSignal = "-";
bool            gDailyBlocked = false;

//+------------------------------------------------------------------+
//| Small helpers                                                    |
//+------------------------------------------------------------------+
string HashId(const string s)
  {
   uint h = 2166136261;
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
     {
      h ^= (uint)StringGetCharacter(s, i);
      h *= 16777619;
     }
   return StringFormat("%08X", h);
  }

string Tag(const string hash)
  {
   return "TVB " + hash;
  }

double GV(const string name, const double def = 0)
  {
   string k = gGV + name;
   return GlobalVariableCheck(k) ? GlobalVariableGet(k) : def;
  }

void SetGV(const string name, const double v)
  {
   GlobalVariableSet(gGV + name, v);
  }

void Journal(const Signal &s, const string status, const ulong ticket, const double lots, const double price,
             const double sl, const double tp, const string detail)
  {
   string quoted = detail;
   StringReplace(quoted, "\"", "\"\"");
   string line = StringFormat("%s,%s,%s,%s,%s,%s,%I64u,%.2f,%s,%s,%s,\"%s\"",
                              TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), s.id, s.hash, s.symbol, s.action,
                              status, ticket, lots, DoubleToString(price, 5), DoubleToString(sl, 5), DoubleToString(tp, 5), quoted);
   PrintFormat("[TVB] %s %s %s: %s %s", s.action, s.symbol, s.id, status, detail);
   int h = FileOpen(gJournal, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return;
   if(FileSize(h) == 0)
      FileWriteString(h, "server_time,id,hash,symbol,action,status,ticket,lots,price,sl,tp,detail\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

void SplitList(const string src, string &out[])
  {
   ArrayResize(out, 0);
   string parts[];
   int n = StringSplit(src, ',', parts);
   for(int i = 0; i < n; i++)
     {
      string p = parts[i];
      StringTrimLeft(p);
      StringTrimRight(p);
      if(p != "")
        {
         int k = ArraySize(out);
         ArrayResize(out, k + 1);
         out[k] = p;
        }
     }
  }

string MapSymbol(const string tv)
  {
   for(int i = 0; i < ArraySize(gMapFrom); i++)
      if(StringCompare(gMapFrom[i], tv, false) == 0)
         return gMapTo[i];
   return tv + InpSymbolSuffix;
  }

bool IsAllowed(const string sym)
  {
   if(ArraySize(gAllowed) == 0)
      return true;
   for(int i = 0; i < ArraySize(gAllowed); i++)
      if(StringCompare(gAllowed[i], sym, false) == 0)
         return true;
   return false;
  }

bool ParseSignal(const string line, Signal &s)
  {
   ZeroMemory(s);
   string parts[];
   int n = StringSplit(line, ';', parts);
   for(int i = 0; i < n; i++)
     {
      int eq = StringFind(parts[i], "=");
      if(eq <= 0)
         continue;
      string k = StringSubstr(parts[i], 0, eq);
      string v = StringSubstr(parts[i], eq + 1);
      StringTrimRight(v);
      if(k == "id")              s.id = v;
      else if(k == "symbol")     s.symbol = v;
      else if(k == "action")     s.action = v;
      else if(k == "comment")    s.comment = v;
      else if(k == "sl")         s.sl = StringToDouble(v);
      else if(k == "tp")         s.tp = StringToDouble(v);
      else if(k == "sl_points")  s.slPts = StringToDouble(v);
      else if(k == "tp_points")  s.tpPts = StringToDouble(v);
      else if(k == "risk")       s.risk = StringToDouble(v);
      else if(k == "lots")       s.lots = StringToDouble(v);
      else if(k == "received")   s.received = StringToDouble(v);
      else if(k == "time")       s.time = (datetime)StringToInteger(v);
     }
   s.hash = HashId(s.id);
   return s.id != "" && s.action != "";
  }

bool ReadSignalFile(const string path, Signal &s)
  {
   int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return false;
   string line = FileReadString(h);
   FileClose(h);
   return ParseSignal(line, s);
  }

void RememberProcessed(const string id)
  {
   gProcessed.Add(id);
   if(InpReplay)
      return;
   int h = FileOpen(gProcessedFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, id + "\r\n");
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
int CountPositions(const string sym, const int type)
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != sym)
         continue;
      if(type < 0 || PositionGetInteger(POSITION_TYPE) == type)
         c++;
     }
   return c;
  }

// sym = "" -> all symbols; type = -1 -> both directions
int ClosePositions(const string sym, const int type)
  {
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym)
         continue;
      if(type >= 0 && PositionGetInteger(POSITION_TYPE) != type)
         continue;
      if(trade.PositionClose(t))
         closed++;
      else
         PrintFormat("[TVB] close #%I64u failed: %u %s", t, trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
   return closed;
  }

// Was this signal already executed? (covers a crash between sending the order and moving the file)
bool AlreadyExecuted(const string hash)
  {
   string tag = Tag(hash);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t != 0 && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic && PositionGetString(POSITION_COMMENT) == tag)
         return true;
     }
   if(!HistorySelect(TimeCurrent() - 7 * 86400, TimeCurrent() + 60))
      return false;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d != 0 && (ulong)HistoryDealGetInteger(d, DEAL_MAGIC) == InpMagic && HistoryDealGetString(d, DEAL_COMMENT) == tag)
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Protection: halt, daily loss, drawdown, kill switch              |
//+------------------------------------------------------------------+
bool IsHalted()
  {
   return GV("halted") > 0.5;
  }

int Halt(const string reason, const int code)
  {
   int closed = ClosePositions("", -1);
   if(!IsHalted())
     {
      SetGV("halted", 1);
      SetGV("halt_code", code);
      string msg = StringFormat("TV Bridge HALTED (%s): closed %d positions, new entries blocked until reset", reason, closed);
      Print("[TVB] ", msg);
      if(!MQLInfoInteger(MQL_TESTER))
         Alert(msg);
     }
   return closed;
  }

string HaltReason()
  {
   int c = (int)GV("halt_code");
   return c == 1 ? "kill switch" : c == 2 ? "max drawdown" : "unknown";
  }

void UpdateProtection()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayKey = (double)(TimeCurrent() / 86400);
   if(GV("day_key") != dayKey)
     {
      SetGV("day_key", dayKey);
      SetGV("day_start_eq", eq);
      SetGV("day_closed", 0);
     }
   double dayStart = GV("day_start_eq", eq);
   gDailyBlocked = InpDailyLossPct > 0 && dayStart > 0 && (dayStart - eq) >= dayStart * InpDailyLossPct / 100.0;
   if(gDailyBlocked && InpCloseOnDaily && GV("day_closed") < 0.5)
     {
      int closed = ClosePositions("", -1);
      SetGV("day_closed", 1);
      string msg = StringFormat("TV Bridge daily loss limit hit (%.2f of %.2f): closed %d positions, no new entries today",
                                dayStart - eq, dayStart * InpDailyLossPct / 100.0, closed);
      Print("[TVB] ", msg);
      if(!MQLInfoInteger(MQL_TESTER))
         Alert(msg);
     }

   double peak = MathMax(GV("peak_eq", eq), eq);
   SetGV("peak_eq", peak);
   if(InpMaxDDPct > 0 && peak > 0 && (peak - eq) / peak * 100.0 >= InpMaxDDPct)
      Halt(StringFormat("drawdown %.1f%% from peak", (peak - eq) / peak * 100.0), 2);

   if(FileIsExist(InpKillFile, FILE_COMMON))
     {
      FileDelete(InpKillFile, FILE_COMMON);
      Halt("remote kill switch", 1);
     }
  }

//+------------------------------------------------------------------+
//| Position size                                                    |
//+------------------------------------------------------------------+
double NormalizeLots(const string sym, const double lots)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = MathMin(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), InpMaxLots);
   if(step <= 0)
      step = 0.01;
   double v = MathFloor(lots / step + 1e-9) * step;
   if(v > vmax)
      v = MathFloor(vmax / step + 1e-9) * step;
   if(v < vmin)
      return 0;
   return NormalizeDouble(v, 8);
  }

double RiskLots(const string sym, const bool isBuy, const double entry, const double sl, const double riskPct)
  {
   double money = AccountInfoDouble(ACCOUNT_EQUITY) * riskPct / 100.0;
   double pl = 0;
   if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, 1.0, entry, sl, pl) || pl == 0)
      return 0;
   return NormalizeLots(sym, money / MathAbs(pl));
  }

//+------------------------------------------------------------------+
//| Execute one signal: every outcome is written to the journal      |
//| (executed, rejected with a reason, duplicate or failed).         |
//+------------------------------------------------------------------+
void Execute(Signal &s)
  {
   gLastSignal = s.action + " " + s.symbol + " " + s.id;
   if(gProcessed.Contains(s.id))
     {
      Journal(s, "duplicate", 0, 0, 0, 0, 0, "id already processed by the EA");
      return;
     }
   RememberProcessed(s.id);

   if(s.action == "kill")
     {
      int c = Halt("kill signal", 1);
      Journal(s, "halted", 0, 0, 0, 0, 0, StringFormat("kill signal, closed %d positions", c));
      return;
     }
   if(s.action == "close_all")
     {
      int c = ClosePositions("", -1);
      Journal(s, "closed", 0, 0, 0, 0, 0, StringFormat("closed %d positions", c));
      return;
     }

   string sym = MapSymbol(s.symbol);
   if(!IsAllowed(sym))
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "symbol " + sym + " is not in the allowed list");
      return;
     }
   if(!SymbolSelect(sym, true) || SymbolInfoDouble(sym, SYMBOL_BID) <= 0)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "unknown symbol " + sym);
      return;
     }
   if(s.action == "close")
     {
      int c = ClosePositions(sym, -1);
      Journal(s, "closed", 0, 0, 0, 0, 0, StringFormat("closed %d positions on %s", c, sym));
      return;
     }
   if(s.action != "buy" && s.action != "sell")
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "unknown action");
      return;
     }

   //--- entries are blocked by halts and by the daily loss limit
   if(IsHalted())
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "bridge is halted (" + HaltReason() + ")");
      return;
     }
   if(gDailyBlocked)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "daily loss limit reached");
      return;
     }
   if(!MQLInfoInteger(MQL_TESTER) && (!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)))
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "algo trading is disabled in the terminal");
      return;
     }
   if(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "trading on " + sym + " is not fully allowed now");
      return;
     }
   if(AlreadyExecuted(s.hash))
     {
      Journal(s, "duplicate", 0, 0, 0, 0, 0, "already executed (found in positions/history)");
      return;
     }
   long spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
   if(InpMaxSpreadPts > 0 && spread > InpMaxSpreadPts)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, StringFormat("spread %d > max %d points", (int)spread, InpMaxSpreadPts));
      return;
     }

   bool   isBuy = s.action == "buy";
   int    dirType = isBuy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   if(CountPositions(sym, dirType) >= InpMaxPositions)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "max positions for this symbol and direction");
      return;
     }

   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   double entry  = isBuy ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);

   double sl = 0;
   if(s.sl > 0)
      sl = s.sl;
   else if(s.slPts > 0)
      sl = isBuy ? entry - s.slPts * point : entry + s.slPts * point;
   else if(InpDefaultSLPts > 0)
      sl = isBuy ? entry - InpDefaultSLPts * point : entry + InpDefaultSLPts * point;
   sl = NormalizeDouble(sl, digits);
   if(sl == 0 && InpSizeMode == SIZE_RISK)
     {
      Journal(s, "rejected", 0, 0, 0, 0, 0, "no stop loss: risk-based size needs one");
      return;
     }
   double minGap = (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(sl != 0 && (isBuy ? sl >= entry - minGap : sl <= entry + minGap))
     {
      Journal(s, "rejected", 0, 0, entry, sl, 0, "stop loss is on the wrong side or too close to the price");
      return;
     }

   double tp = 0;
   if(s.tp > 0)
      tp = s.tp;
   else if(s.tpPts > 0)
      tp = isBuy ? entry + s.tpPts * point : entry - s.tpPts * point;
   else if(InpDefaultTPR > 0 && sl != 0)
      tp = isBuy ? entry + InpDefaultTPR * (entry - sl) : entry - InpDefaultTPR * (sl - entry);
   tp = NormalizeDouble(tp, digits);
   if(tp != 0 && (isBuy ? tp <= entry + minGap : tp >= entry - minGap))
     {
      Journal(s, "rejected", 0, 0, entry, sl, tp, "take profit is on the wrong side or too close to the price");
      return;
     }

   double lots;
   if(InpSizeMode == SIZE_RISK)
     {
      double riskPct = s.risk > 0 ? MathMin(s.risk, InpMaxRiskPercent) : InpRiskPercent;
      lots = RiskLots(sym, isBuy, entry, sl, riskPct);
     }
   else
      lots = NormalizeLots(sym, s.lots > 0 ? s.lots : InpFixedLots);
   if(lots <= 0)
     {
      Journal(s, "rejected", 0, 0, entry, sl, tp, "size is below the broker minimum lot");
      return;
     }
   double margin = 0;
   if(OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, lots, entry, margin) &&
      margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      Journal(s, "rejected", 0, lots, entry, sl, tp, StringFormat("not enough free margin (needs %.2f)", margin));
      return;
     }

   //--- the signal is valid: only now touch existing positions (an invalid signal must never close anything)
   int closedOpp = 0;
   if(InpCloseOpposite)
      closedOpp = ClosePositions(sym, isBuy ? POSITION_TYPE_SELL : POSITION_TYPE_BUY);

   //--- send, retrying only on price movement; check by tag before any retry so a timeout cannot double the trade
   bool ok = false;
   for(int attempt = 0; attempt < 3 && !ok; attempt++)
     {
      if(attempt > 0 && AlreadyExecuted(s.hash))
        {
         ok = true;
         break;
        }
      ok = isBuy ? trade.Buy(lots, sym, 0, sl, tp, Tag(s.hash)) : trade.Sell(lots, sym, 0, sl, tp, Tag(s.hash));
      uint rc = trade.ResultRetcode();
      if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED))
         break;
      ok = false;
      if(rc != TRADE_RETCODE_REQUOTE && rc != TRADE_RETCODE_PRICE_CHANGED && rc != TRADE_RETCODE_PRICE_OFF)
         break;
      Sleep(200);
     }
   if(ok)
      Journal(s, "executed", trade.ResultOrder(), lots, trade.ResultPrice(), sl, tp,
              StringFormat("risk %.2f%%, spread %d, closed opposite %d", InpSizeMode == SIZE_RISK ? (s.risk > 0 ? MathMin(s.risk, InpMaxRiskPercent) : InpRiskPercent) : 0.0,
                           (int)spread, closedOpp));
   else
      Journal(s, "failed", 0, lots, entry, sl, tp, StringFormat("server: %u %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()));
  }

//+------------------------------------------------------------------+
//| Inbox (live) and replay (tester)                                 |
//+------------------------------------------------------------------+
void ProcessInbox()
  {
   string names[];
   string name;
   long h = FileFindFirst(InpInbox + "\\*.sig", name, FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   do
     {
      int k = ArraySize(names);
      ArrayResize(names, k + 1);
      names[k] = name;
     }
   while(FileFindNext(h, name));
   FileFindClose(h);
   // file names start with the receive time in ms, so sorting by name is chronological
   for(int i = 1; i < ArraySize(names); i++)
     {
      string cur = names[i];
      int j = i - 1;
      while(j >= 0 && StringCompare(names[j], cur) > 0)
        {
         names[j + 1] = names[j];
         j--;
        }
      names[j + 1] = cur;
     }

   for(int i = 0; i < ArraySize(names); i++)
     {
      string path = InpInbox + "\\" + names[i];
      Signal s;
      string dest = "tvb_done\\" + names[i];
      if(!ReadSignalFile(path, s))
        {
         Print("[TVB] unreadable signal file ", names[i]);
         FileMove(path, FILE_COMMON, "tvb_rejected\\" + names[i], FILE_COMMON | FILE_REWRITE);
         continue;
        }
      double age = (double)TimeGMT() - s.received;
      if(InpMaxAgeSec > 0 && age > InpMaxAgeSec)
        {
         Journal(s, "rejected", 0, 0, 0, 0, 0, StringFormat("stale: %.0f s old", age));
         RememberProcessed(s.id);
         dest = "tvb_rejected\\" + names[i];
        }
      else
         Execute(s);
      FileMove(path, FILE_COMMON, dest, FILE_COMMON | FILE_REWRITE);
     }
  }

void LoadReplay()
  {
   ArrayResize(gReplay, 0);
   string name;
   long h = FileFindFirst("tvb_replay\\*.sig", name, FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   do
     {
      Signal s;
      if(ReadSignalFile("tvb_replay\\" + name, s))
        {
         int k = ArraySize(gReplay);
         ArrayResize(gReplay, k + 1);
         gReplay[k] = s;
        }
     }
   while(FileFindNext(h, name));
   FileFindClose(h);
   // sort by signal time (insertion sort, small arrays)
   for(int i = 1; i < ArraySize(gReplay); i++)
     {
      Signal cur = gReplay[i];
      int j = i - 1;
      while(j >= 0 && gReplay[j].time > cur.time)
        {
         gReplay[j + 1] = gReplay[j];
         j--;
        }
      gReplay[j + 1] = cur;
     }
   PrintFormat("[TVB] replay: %d signals loaded", ArraySize(gReplay));
  }

void ProcessReplay()
  {
   datetime now = TimeCurrent();
   for(int i = 0; i < ArraySize(gReplay); i++)
     {
      if(gReplay[i].done || gReplay[i].time > now)
         continue;
      gReplay[i].done = true;
      Execute(gReplay[i]);
     }
  }

//+------------------------------------------------------------------+
//| Panel and kill button                                            |
//+------------------------------------------------------------------+
int CountAllPositions()
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t != 0 && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         c++;
     }
   return c;
  }

void DrawPanel()
  {
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayStart = GV("day_start_eq", eq);
   double peak = GV("peak_eq", eq);
   string state = IsHalted() ? "HALTED (" + HaltReason() + ")" : gDailyBlocked ? "DAILY LIMIT - entries blocked" : "ACTIVE";
   Comment(StringFormat("TradingView -> MT5 bridge   magic %I64u\nState: %s\nToday P/L: %.2f (limit -%.2f)\nDrawdown from peak: %.2f%% (max %.1f%%)\nBridge positions: %d\nLast signal: %s",
                        InpMagic, state, eq - dayStart, InpDailyLossPct > 0 ? dayStart * InpDailyLossPct / 100.0 : 0,
                        peak > 0 ? (peak - eq) / peak * 100.0 : 0, InpMaxDDPct, CountAllPositions(), gLastSignal));
  }

void CreateKillButton()
  {
   string n = "TVB_KILL";
   if(ObjectFind(0, n) < 0)
      ObjectCreate(0, n, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, n, OBJPROP_XDISTANCE, 170);
   ObjectSetInteger(0, n, OBJPROP_YDISTANCE, 30);
   ObjectSetInteger(0, n, OBJPROP_XSIZE, 160);
   ObjectSetInteger(0, n, OBJPROP_YSIZE, 28);
   ObjectSetString(0, n, OBJPROP_TEXT, "EMERGENCY STOP");
   ObjectSetInteger(0, n, OBJPROP_BGCOLOR, clrFireBrick);
   ObjectSetInteger(0, n, OBJPROP_COLOR, clrWhite);
  }

//+------------------------------------------------------------------+
//| Standard handlers                                                |
//+------------------------------------------------------------------+
int OnInit()
  {
   gGV = StringFormat("TVB_%I64u_", InpMagic);
   gJournal = InpReplay ? "tvb_journal_replay.csv" : StringFormat("tvb_journal_%I64u.csv", InpMagic);
   gProcessedFile = StringFormat("tvb_processed_%I64u.txt", InpMagic);

   string pairs[];
   SplitList(InpSymbolMap, pairs);
   ArrayResize(gMapFrom, 0);
   ArrayResize(gMapTo, 0);
   for(int i = 0; i < ArraySize(pairs); i++)
     {
      int eq = StringFind(pairs[i], "=");
      if(eq <= 0)
         continue;
      int k = ArraySize(gMapFrom);
      ArrayResize(gMapFrom, k + 1);
      ArrayResize(gMapTo, k + 1);
      gMapFrom[k] = StringSubstr(pairs[i], 0, eq);
      gMapTo[k] = StringSubstr(pairs[i], eq + 1);
     }
   SplitList(InpAllowed, gAllowed);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetAsyncMode(false);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   if(InpResetHalt)
     {
      SetGV("halted", 0);
      SetGV("halt_code", 0);
      Print("[TVB] halt cleared by input");
     }

   if(InpReplay)
     {
      if(FileIsExist(gJournal, FILE_COMMON))
         FileDelete(gJournal, FILE_COMMON);
      LoadReplay();
     }
   else
     {
      FolderCreate(InpInbox, FILE_COMMON);
      FolderCreate("tvb_done", FILE_COMMON);
      FolderCreate("tvb_rejected", FILE_COMMON);
      int h = FileOpen(gProcessedFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
      if(h != INVALID_HANDLE)
        {
         while(!FileIsEnding(h))
           {
            string id = FileReadString(h);
            if(id != "")
               gProcessed.Add(id);
           }
         FileClose(h);
        }
      CreateKillButton();
     }
   UpdateProtection();
   EventSetMillisecondTimer(MathMax(InpPollMs, 100));
   PrintFormat("[TVB] started: %s, %d processed ids remembered, state %s", InpReplay ? "REPLAY" : "LIVE",
               gProcessed.Count(), IsHalted() ? "HALTED" : "active");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectDelete(0, "TVB_KILL");
   Comment("");
  }

void Cycle()
  {
   UpdateProtection();
   if(InpReplay)
      ProcessReplay();
   else
      ProcessInbox();
   DrawPanel();
  }

void OnTimer()
  {
   Cycle();
  }

void OnTick()
  {
   if(InpReplay)
      Cycle();
  }

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == "TVB_KILL")
     {
      Halt("emergency stop button", 1);
      ObjectSetInteger(0, "TVB_KILL", OBJPROP_STATE, false);
      DrawPanel();
     }
  }
//+------------------------------------------------------------------+
