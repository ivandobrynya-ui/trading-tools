//+------------------------------------------------------------------+
//| EMA_Cross_EA.mq5                                                 |
//| EMA 20 / EMA 50 crossover robot                                  |
//| - signals only on closed candles, entry on the next candle        |
//| - never two trades from one signal (restart-safe)                 |
//| - one position per symbol + magic number                          |
//| - configurable lot, SL, TP, spread filter, magic number           |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "EMA crossover robot: BUY when the fast EMA crosses above the slow EMA,"
#property description "SELL when it crosses below. Closed candles only, one position at a time."

#include <Trade\Trade.mqh>

input group "Signal"
input int  InpFastPeriod = 20;   // Fast EMA period
input int  InpSlowPeriod = 50;   // Slow EMA period
input bool InpReverse    = true; // On an opposite signal close the position and reverse

input group "Orders"
input double InpLots          = 0.10;  // Lot size
input int    InpStopLossPts   = 300;   // Stop loss (points, 0 = none)
input int    InpTakeProfitPts = 600;   // Take profit (points, 0 = none)
input int    InpMaxSpreadPts  = 20;    // Max spread (points, 0 = off)
input int    InpSlippagePts   = 10;    // Max slippage (points)
input ulong  InpMagic         = 2050;  // Magic number

CTrade   trade;
int      hFast = INVALID_HANDLE, hSlow = INVALID_HANDLE;
datetime gLastLogBar = 0;

//--- the position of this EA on this symbol: 1 long, -1 short, 0 none
int CurrentPosition(ulong &ticket)
  {
   ticket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ticket = t;
      return PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
     }
   return 0;
  }

//--- did this EA already open a trade on the current bar? (read from the server: survives restarts)
bool EnteredOnThisBar(const datetime barOpen)
  {
   if(!HistorySelect(barOpen, TimeCurrent() + 60))
      return false;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      if(HistoryDealGetString(d, DEAL_SYMBOL) == _Symbol && (ulong)HistoryDealGetInteger(d, DEAL_MAGIC) == InpMagic &&
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return true;
     }
   return false;
  }

void LogOnce(const datetime bar, const string msg)
  {
   if(bar == gLastLogBar)
      return;
   gLastLogBar = bar;
   Print(msg);
  }

int OnInit()
  {
   if(InpFastPeriod < 1 || InpSlowPeriod <= InpFastPeriod || InpLots <= 0)
     {
      Print("EMA_Cross: fast period must be >= 1, slow period greater than fast, lots > 0");
      return INIT_PARAMETERS_INCORRECT;
     }
   hFast = iMA(_Symbol, PERIOD_CURRENT, InpFastPeriod, 0, MODE_EMA, PRICE_CLOSE);
   hSlow = iMA(_Symbol, PERIOD_CURRENT, InpSlowPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE)
      return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
  }

void OnTick()
  {
   //--- the signal comes from the two last CLOSED candles (index 1 and 2), so it never changes during the bar
   double f[2], s[2];
   if(CopyBuffer(hFast, 0, 1, 2, f) != 2 || CopyBuffer(hSlow, 0, 1, 2, s) != 2)
      return;
   // f[0], s[0] = candle 2 (older); f[1], s[1] = candle 1 (just closed)
   bool crossUp   = f[0] <= s[0] && f[1] > s[1];
   bool crossDown = f[0] >= s[0] && f[1] < s[1];
   if(!crossUp && !crossDown)
      return;

   datetime barOpen = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(EnteredOnThisBar(barOpen))
      return; // this signal was already traded (also after a restart)

   ulong ticket;
   int pos = CurrentPosition(ticket);
   int want = crossUp ? 1 : -1;
   if(pos == want)
      return; // already positioned in the signal direction
   if(pos != 0 && !InpReverse)
      return; // one position at a time

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPts > 0 && spread > InpMaxSpreadPts)
     {
      LogOnce(barOpen, StringFormat("EMA_Cross: %s signal waits, spread %d > %d points", crossUp ? "BUY" : "SELL", (int)spread, InpMaxSpreadPts));
      return; // try again on the next tick of this candle
     }

   if(pos != 0 && !trade.PositionClose(ticket))
     {
      PrintFormat("EMA_Cross: closing the opposite position failed: %u %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
     }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double price = crossUp ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = InpStopLossPts > 0 ? (crossUp ? price - InpStopLossPts * point : price + InpStopLossPts * point) : 0;
   double tp = InpTakeProfitPts > 0 ? (crossUp ? price + InpTakeProfitPts * point : price - InpTakeProfitPts * point) : 0;
   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   bool ok = crossUp ? trade.Buy(InpLots, _Symbol, 0, sl, tp, "EMA cross")
                     : trade.Sell(InpLots, _Symbol, 0, sl, tp, "EMA cross");
   if(ok)
      PrintFormat("EMA_Cross: %s %.2f at %s (signal candle %s)", crossUp ? "BUY" : "SELL", InpLots,
                  DoubleToString(trade.ResultPrice(), _Digits), TimeToString(iTime(_Symbol, PERIOD_CURRENT, 1)));
   else
      PrintFormat("EMA_Cross: order failed: %u %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
  }
//+------------------------------------------------------------------+
