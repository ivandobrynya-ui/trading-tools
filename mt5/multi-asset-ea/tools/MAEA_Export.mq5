//+------------------------------------------------------------------+
//| MAEA_Export.mq5 (script)                                         |
//| 1) every symbol of the trade server -> Common\Files CSV          |
//| 2) bars of a list of symbols -> one CSV per symbol               |
//| Used to pick symbols and to re-check Multi_Asset_EA signals      |
//| offline. Closes the terminal at the end (run via [StartUp]).     |
//+------------------------------------------------------------------+
#property copyright "Ivan Semernya"
#property version   "1.00"

input string          InpSymbols   = "";              // Symbols to export bars for (comma separated, empty = list only)
input ENUM_TIMEFRAMES InpTf        = PERIOD_H1;       // Bars timeframe
input datetime        InpFrom      = D'2023.06.01';   // From
input datetime        InpTo        = D'2026.10.17';   // To
input string          InpPrefix    = "maea_";         // File name prefix
input bool            InpCloseTerm = true;            // Close the terminal when done

string Clean(const string s)
  {
   string t = s;
   StringReplace(t, ",", ";");
   StringReplace(t, "\r", " ");
   StringReplace(t, "\n", " ");
   return t;
  }

void ListSymbols()
  {
   int h = FileOpen(InpPrefix + "symbols.csv", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   PrintFormat("MAEA_Export: open %s -> handle %d, error %d", InpPrefix + "symbols.csv", h, GetLastError());
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, "name,path,description,contract,trade_mode,calc_mode,currency_profit,volume_min,volume_step\r\n");
   int n = SymbolsTotal(false);
   for(int i = 0; i < n; i++)
     {
      string s = SymbolName(i, false);
      FileWriteString(h, StringFormat("%s,%s,%s,%s,%s,%s,%s,%s,%s\r\n", s, Clean(SymbolInfoString(s, SYMBOL_PATH)),
                                      Clean(SymbolInfoString(s, SYMBOL_DESCRIPTION)),
                                      DoubleToString(SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE), 2),
                                      EnumToString((ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(s, SYMBOL_TRADE_MODE)),
                                      EnumToString((ENUM_SYMBOL_CALC_MODE)SymbolInfoInteger(s, SYMBOL_TRADE_CALC_MODE)),
                                      SymbolInfoString(s, SYMBOL_CURRENCY_PROFIT),
                                      DoubleToString(SymbolInfoDouble(s, SYMBOL_VOLUME_MIN), 2),
                                      DoubleToString(SymbolInfoDouble(s, SYMBOL_VOLUME_STEP), 2)));
     }
   FileClose(h);
   PrintFormat("MAEA_Export: %d symbols listed", n);
  }

void ExportBars(const string sym)
  {
   SymbolSelect(sym, true);
   MqlRates r[];
   int n = -1;
   for(int t = 0; t < 120 && n <= 0; t++)
     {
      n = CopyRates(sym, InpTf, InpFrom, InpTo, r);
      if(n <= 0)
         Sleep(500);
     }
   if(n <= 0)
     {
      PrintFormat("MAEA_Export: no bars for %s, error %d", sym, GetLastError());
      return;
     }
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   int h = FileOpen(InpPrefix + "bars_" + sym + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, "time,open,high,low,close,tick_volume,spread\r\n");
   for(int i = 0; i < n; i++)
      FileWriteString(h, StringFormat("%I64d,%s,%s,%s,%s,%I64d,%d\r\n", (long)r[i].time, DoubleToString(r[i].open, digits),
                                      DoubleToString(r[i].high, digits), DoubleToString(r[i].low, digits),
                                      DoubleToString(r[i].close, digits), r[i].tick_volume, r[i].spread));
   FileClose(h);
   PrintFormat("MAEA_Export: %d bars of %s", n, sym);
  }

void OnStart()
  {
   // a script started from [StartUp] can run before the terminal has synchronized the symbol list
   for(int t = 0; t < 120 && !IsStopped() && (!TerminalInfoInteger(TERMINAL_CONNECTED) || SymbolsTotal(false) < 10); t++)
      Sleep(500);
   PrintFormat("MAEA_Export: connected=%d, %d symbols on the server", (int)TerminalInfoInteger(TERMINAL_CONNECTED), SymbolsTotal(false));
   ListSymbols();
   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   for(int k = 0; k < n; k++)
     {
      StringTrimLeft(parts[k]);
      StringTrimRight(parts[k]);
      if(parts[k] != "")
         ExportBars(parts[k]);
     }
   Print("MAEA_Export: done");
   if(InpCloseTerm)
      TerminalClose(0);
  }
//+------------------------------------------------------------------+
