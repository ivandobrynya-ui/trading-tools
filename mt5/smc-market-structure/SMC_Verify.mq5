//+------------------------------------------------------------------+
//| SMC_Verify.mq5 (script)                                          |
//| Runs SMC_Market_Structure on H1 bars and writes its signal       |
//| buffers (and the bars) to a CSV for a bar-by-bar comparison.     |
//| Mode 0: an existing symbol (e.g. XAUUSD) as it is in the terminal|
//| Mode 1: bars from a CSV (e.g. exported from TradingView), loaded |
//|         into a custom symbol so both platforms see the same data |
//+------------------------------------------------------------------+
#property copyright "Ivan Semernya"
#property version   "1.10"

enum ENUM_VERIFY_MODE
  {
   MODE_SYMBOL = 0, // Existing symbol
   MODE_CSV    = 1  // Bars from CSV -> custom symbol
  };

input ENUM_VERIFY_MODE InpMode      = MODE_SYMBOL;        // Data source
input string           InpSymbol    = "XAUUSD";           // Symbol (mode 0) or custom symbol name (mode 1)
input string           InpCsv       = "tv_bars.csv";      // Mode 1: bars in Common\Files (time,open,high,low,close)
input int              InpDigits    = 3;                  // Mode 1: price digits of the custom symbol
input string           InpOut       = "mt5_signals.csv";  // Output file in Common\Files
input bool             InpCloseTerm = true;               // Close the terminal when done (command-line runs)

void Finish(const int code)
  {
   if(InpCloseTerm)
      TerminalClose(code);
  }

bool LoadCsvIntoCustomSymbol(int &n)
  {
   int h = FileOpen(InpCsv, FILE_READ | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      Print("SMC_Verify: cannot open ", InpCsv, " error ", GetLastError());
      return false;
     }
   for(int k = 0; k < 5; k++)
      FileReadString(h);
   MqlRates rates[];
   n = 0;
   while(!FileIsEnding(h))
     {
      string ts = FileReadString(h);
      if(ts == "")
         break;
      MqlRates r;
      ZeroMemory(r);
      r.time  = (datetime)StringToInteger(ts);
      r.open  = StringToDouble(FileReadString(h));
      r.high  = StringToDouble(FileReadString(h));
      r.low   = StringToDouble(FileReadString(h));
      r.close = StringToDouble(FileReadString(h));
      r.tick_volume = 1;
      ArrayResize(rates, n + 1, 10000);
      rates[n++] = r;
     }
   FileClose(h);
   if(n < 100)
      return false;
   bool isCustom = false;
   if(!SymbolExist(InpSymbol, isCustom) && !CustomSymbolCreate(InpSymbol, "Custom\\Verify", NULL))
     {
      Print("SMC_Verify: CustomSymbolCreate failed ", GetLastError());
      return false;
     }
   CustomSymbolSetInteger(InpSymbol, SYMBOL_DIGITS, InpDigits);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_POINT, MathPow(10.0, -InpDigits));
   CustomSymbolSetDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE, MathPow(10.0, -InpDigits));
   // each H1 bar is stored as one M1 bar at the hour, so the H1 chart rebuilds it unchanged
   if(CustomRatesReplace(InpSymbol, 0, rates[n - 1].time + 3600, rates) < 0)
     {
      Print("SMC_Verify: CustomRatesReplace failed ", GetLastError());
      return false;
     }
   return true;
  }

void OnStart()
  {
   int expected = 0;
   if(InpMode == MODE_CSV && !LoadCsvIntoCustomSymbol(expected))
     {
      Finish(1);
      return;
     }
   SymbolSelect(InpSymbol, true);

   //--- wait until H1 history is available
   int bars = 0;
   for(int t = 0; t < 240; t++)
     {
      bars = Bars(InpSymbol, PERIOD_H1);
      if(bars > 0 && (InpMode == MODE_SYMBOL || bars >= expected) && SeriesInfoInteger(InpSymbol, PERIOD_H1, SERIES_SYNCHRONIZED))
         break;
      Sleep(250);
     }
   PrintFormat("SMC_Verify: %s H1 bars: %d", InpSymbol, bars);

   int ind = iCustom(InpSymbol, PERIOD_H1, "Portfolio\\SMC_Market_Structure");
   if(ind == INVALID_HANDLE)
     {
      Print("SMC_Verify: iCustom failed ", GetLastError());
      Finish(1);
      return;
     }
   for(int t = 0; t < 480; t++)
     {
      if(BarsCalculated(ind) >= bars)
         break;
      Sleep(250);
     }
   int calc = BarsCalculated(ind);
   if(calc < bars)
     {
      PrintFormat("SMC_Verify: indicator calculated only %d of %d bars", calc, bars);
      Finish(1);
      return;
     }

   MqlRates r[];
   if(CopyRates(InpSymbol, PERIOD_H1, 0, bars, r) != bars)
     {
      Print("SMC_Verify: CopyRates failed ", GetLastError());
      Finish(1);
      return;
     }
   double all[];
   double tmp[];
   ArrayResize(all, bars * 8);
   for(int k = 0; k < 8; k++)
     {
      // the indicator may still be recalculating after a history update: retry for a while
      int copied = -1;
      for(int t = 0; t < 120 && copied != bars; t++)
        {
         copied = CopyBuffer(ind, 5 + k, 0, bars, tmp);
         if(copied != bars)
            Sleep(250);
        }
      if(copied != bars)
        {
         Print("SMC_Verify: CopyBuffer failed for buffer ", 5 + k, " error ", GetLastError());
         Finish(1);
         return;
        }
      for(int i = 0; i < bars; i++)
         all[i * 8 + k] = tmp[i];
     }

   int o = FileOpen(InpOut, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(o == INVALID_HANDLE)
     {
      Print("SMC_Verify: cannot write ", InpOut);
      Finish(1);
      return;
     }
   int digits = (int)SymbolInfoInteger(InpSymbol, SYMBOL_DIGITS);
   FileWriteString(o, "time,open,high,low,close,swBull,swBear,intBull,intBear,eqh,eql,pivH,pivL\r\n");
   for(int i = 0; i < bars; i++)
     {
      string line = IntegerToString((long)r[i].time) + "," + DoubleToString(r[i].open, digits) + "," +
                    DoubleToString(r[i].high, digits) + "," + DoubleToString(r[i].low, digits) + "," +
                    DoubleToString(r[i].close, digits);
      for(int k = 0; k < 8; k++)
        {
         double v = all[i * 8 + k];
         line += "," + IntegerToString(v == EMPTY_VALUE ? 0 : (int)MathRound(v));
        }
      FileWriteString(o, line + "\r\n");
     }
   FileClose(o);
   PrintFormat("SMC_Verify: wrote %d rows to %s", bars, InpOut);
   IndicatorRelease(ind);
   Finish(0);
  }
//+------------------------------------------------------------------+
