//+------------------------------------------------------------------+
//| ExportBars.mq5 (script)                                          |
//| Writes bars of a symbol/timeframe to Common\Files as CSV         |
//| (time,open,high,low,close,tick_volume,spread) for offline checks |
//+------------------------------------------------------------------+
#property copyright "Ivan Semernya"
#property version   "1.00"

input string          InpSymbol    = "EURUSD";
input ENUM_TIMEFRAMES InpTf        = PERIOD_M15;
input datetime        InpFrom      = D'2024.01.01';
input datetime        InpTo        = D'2026.10.17';
input string          InpOut       = "bars.csv";
input bool            InpCloseTerm = true;

void OnStart()
  {
   SymbolSelect(InpSymbol, true);
   MqlRates r[];
   int n = -1;
   for(int t = 0; t < 120 && n <= 0; t++)
     {
      n = CopyRates(InpSymbol, InpTf, InpFrom, InpTo, r);
      if(n <= 0)
         Sleep(500);
     }
   if(n <= 0)
     {
      Print("ExportBars: no data ", GetLastError());
      if(InpCloseTerm)
         TerminalClose(1);
      return;
     }
   int digits = (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS);
   int h = FileOpen(InpOut, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   FileWriteString(h, "time,open,high,low,close,tick_volume,spread\r\n");
   for(int i = 0; i < n; i++)
      FileWriteString(h, StringFormat("%I64d,%s,%s,%s,%s,%I64d,%d\r\n", (long)r[i].time, DoubleToString(r[i].open, digits),
                                      DoubleToString(r[i].high, digits), DoubleToString(r[i].low, digits),
                                      DoubleToString(r[i].close, digits), r[i].tick_volume, r[i].spread));
   FileClose(h);
   PrintFormat("ExportBars: %d bars of %s %s -> %s", n, InpSymbol, EnumToString(InpTf), InpOut);
   if(InpCloseTerm)
      TerminalClose(0);
  }
//+------------------------------------------------------------------+
