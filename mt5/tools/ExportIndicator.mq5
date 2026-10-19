//+------------------------------------------------------------------+
//| ExportIndicator.mq5 (script)                                     |
//| Runs any custom indicator (default inputs) via iCustom and writes|
//| bar time + its first N buffers to Common\Files as CSV.           |
//+------------------------------------------------------------------+
#property copyright "Ivan Semernya"
#property version   "1.00"

input string          InpIndicator = "Practice\\Volume_Profile"; // Indicator path under MQL5\Indicators
input string          InpSymbol    = "XAUUSD";
input ENUM_TIMEFRAMES InpTf        = PERIOD_H1;
input int             InpBuffers   = 6;       // Number of buffers to export (from 0)
input int             InpBars      = 2000;    // Last N bars
input string          InpOut       = "indicator.csv";
input int             InpPreloadDays = 90;   // Load this much history before creating the indicator
input bool            InpCloseTerm = true;

void Done(const int code)
  {
   if(InpCloseTerm)
      TerminalClose(code);
  }

void OnStart()
  {
   SymbolSelect(InpSymbol, true);
   // load the history the indicator needs (chart timeframe and M1) BEFORE creating it:
   // on a closed market there are no ticks to trigger a recalculation later
   ENUM_TIMEFRAMES tfs[2];
   tfs[0] = InpTf;
   tfs[1] = PERIOD_M1;
   for(int k = 0; k < 2; k++)
     {
      MqlRates probe[];
      datetime from = TimeCurrent() - (datetime)InpPreloadDays * 86400;
      for(int t = 0; t < 240; t++)
        {
         int got = CopyRates(InpSymbol, tfs[k], from, TimeCurrent(), probe); // a script may wait for history
         if(got > 0 && (datetime)SeriesInfoInteger(InpSymbol, tfs[k], SERIES_FIRSTDATE) <= from + 7 * 86400)
            break;
         Sleep(250);
        }
      PrintFormat("ExportIndicator: %s history from %s", EnumToString(tfs[k]),
                  TimeToString((datetime)SeriesInfoInteger(InpSymbol, tfs[k], SERIES_FIRSTDATE)));
     }
   int h = iCustom(InpSymbol, InpTf, InpIndicator);
   if(h == INVALID_HANDLE)
     {
      Print("ExportIndicator: iCustom failed ", GetLastError());
      Done(1);
      return;
     }
   int bars = MathMin(InpBars, Bars(InpSymbol, InpTf));
   for(int t = 0; t < 120 && BarsCalculated(h) < bars; t++)
      Sleep(250);
   datetime tm[];
   if(CopyTime(InpSymbol, InpTf, 0, bars, tm) != bars)
     {
      Print("ExportIndicator: CopyTime failed");
      Done(1);
      return;
     }
   double all[];
   ArrayResize(all, bars * InpBuffers);
   double tmp[];
   for(int k = 0; k < InpBuffers; k++)
     {
      int copied = -1;
      for(int t = 0; t < 20 && copied != bars; t++)
        {
         copied = CopyBuffer(h, k, 0, bars, tmp);
         if(copied != bars)
            Sleep(250);
        }
      if(copied != bars)
        {
         PrintFormat("ExportIndicator: CopyBuffer %d failed %d", k, GetLastError());
         Done(1);
         return;
        }
      for(int i = 0; i < bars; i++)
         all[i * InpBuffers + k] = tmp[i];
     }
   int o = FileOpen(InpOut, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   string head = "time";
   for(int k = 0; k < InpBuffers; k++)
      head += ",b" + IntegerToString(k);
   FileWriteString(o, head + "\r\n");
   for(int i = 0; i < bars; i++)
     {
      string line = IntegerToString((long)tm[i]);
      for(int k = 0; k < InpBuffers; k++)
        {
         double v = all[i * InpBuffers + k];
         line += "," + (v == EMPTY_VALUE ? "" : DoubleToString(v, 8));
        }
      FileWriteString(o, line + "\r\n");
     }
   FileClose(o);
   PrintFormat("ExportIndicator: %d bars x %d buffers -> %s", bars, InpBuffers, InpOut);
   IndicatorRelease(h);
   Done(0);
  }
//+------------------------------------------------------------------+
