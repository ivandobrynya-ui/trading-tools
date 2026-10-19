//+------------------------------------------------------------------+
//| Volume_Profile.mq5                                               |
//| Session volume profile: POC, value area (VAH / VAL) and the      |
//| previous session's levels. Built from M1 bars.                   |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "Daily volume profile from M1 bars: developing POC / VAH / VAL and the previous"
#property description "session's POC / VAH / VAL as levels. Buffers are readable by EAs via iCustom."
#property indicator_chart_window
#property indicator_buffers 6
#property indicator_plots   6

#property indicator_label1  "POC (developing)"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrGold
#property indicator_style1  STYLE_DOT
#property indicator_label2  "VAH (developing)"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrDodgerBlue
#property indicator_style2  STYLE_DOT
#property indicator_label3  "VAL (developing)"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrDodgerBlue
#property indicator_style3  STYLE_DOT
#property indicator_label4  "Previous POC"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrOrange
#property indicator_width4  2
#property indicator_label5  "Previous VAH"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrMediumSeaGreen
#property indicator_width5  2
#property indicator_label6  "Previous VAL"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrIndianRed
#property indicator_width6  2

input int    InpRowPoints  = 0;     // Row height in points (0 = auto from the previous session range)
input int    InpAutoRows   = 100;   // Auto mode: rows per previous session range
input double InpVAPercent  = 70.0;  // Value area, % of session volume
input int    InpSessionHour = 0;    // Session starts at this server hour (0 = server midnight)
input int    InpMaxDays    = 60;    // Sessions to calculate (older bars stay empty)
input bool   InpHistogram  = true;  // Draw the profile histogram for the current and previous session
input int    InpHistWidth  = 30;    // Histogram width, % of the session length

double bPoc[], bVah[], bVal[], bPPoc[], bPVah[], bPVal[];

struct Profile
  {
   datetime          day;
   bool              ok;
   double            base;   // lower edge of row 0
   double            row;    // row height
   double            vol[];
   double            hi, lo; // session range
  };

Profile S;         // current session
bool    gPrevOk = false;
double  gPPoc, gPVah, gPVal;
double  gPrevRange = 0;
int     gLastDone = -1;
string  gPrefix = "VP_";
datetime gLastDraw = 0;
bool    gDataMissing = false; // M1 history not loaded yet: report 'not calculated' and retry

//+------------------------------------------------------------------+
datetime SessionDay(const datetime t)
  {
   datetime shifted = t - InpSessionHour * 3600;
   return shifted - (shifted % 86400) + InpSessionHour * 3600;
  }

double TickSize()
  {
   double t = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   return t > 0 ? t : _Point;
  }

double RowHeight()
  {
   double tick = TickSize();
   if(InpRowPoints > 0)
      return InpRowPoints * _Point;
   double r = gPrevRange > 0 ? gPrevRange / InpAutoRows : tick * InpAutoRows;
   return MathMax(tick, MathCeil(r / tick) * tick);
  }

void StartSession(const datetime day)
  {
   S.day = day;
   S.ok = false;
   S.row = RowHeight();
   S.base = 0;
   S.hi = 0;
   S.lo = 0;
   ArrayResize(S.vol, 0);
  }

// spreads the bar's volume evenly over the rows its range touches
void AddBar(Profile &p, const double high, const double low, const double volume)
  {
   if(volume <= 0 || high < low)
      return;
   if(!p.ok)
     {
      p.base = MathFloor(low / p.row) * p.row;
      p.ok = true;
      p.hi = high;
      p.lo = low;
     }
   p.hi = MathMax(p.hi, high);
   p.lo = MathMin(p.lo, low);
   int lo = (int)MathFloor((low - p.base) / p.row + 1e-9);
   int hi = (int)MathFloor((high - p.base) / p.row + 1e-9);
   if(lo < 0)
     {
      int shift = -lo, n = ArraySize(p.vol);
      ArrayResize(p.vol, n + shift);
      for(int k = n - 1; k >= 0; k--)
         p.vol[k + shift] = p.vol[k];
      for(int k = 0; k < shift; k++)
         p.vol[k] = 0;
      p.base -= shift * p.row;
      lo += shift;
      hi += shift;
     }
   if(hi >= ArraySize(p.vol))
     {
      int n = ArraySize(p.vol);
      ArrayResize(p.vol, hi + 1);
      for(int k = n; k <= hi; k++)
         p.vol[k] = 0;
     }
   double share = volume / (hi - lo + 1);
   for(int k = lo; k <= hi; k++)
      p.vol[k] += share;
  }

// POC = row with the most volume (lowest row on ties); value area grows from the POC towards
// the larger neighbour until it holds InpVAPercent of the volume
bool ValueArea(const Profile &p, double &poc, double &vah, double &val)
  {
   int n = ArraySize(p.vol);
   if(!p.ok || n == 0)
      return false;
   double total = 0;
   int pocIdx = 0;
   for(int k = 0; k < n; k++)
     {
      total += p.vol[k];
      if(p.vol[k] > p.vol[pocIdx])
         pocIdx = k;
     }
   if(total <= 0)
      return false;
   double target = total * InpVAPercent / 100.0, acc = p.vol[pocIdx];
   int lo = pocIdx, hi = pocIdx;
   while(acc < target && (lo > 0 || hi < n - 1))
     {
      double up = hi + 1 < n ? p.vol[hi + 1] : -1.0;
      double dn = lo - 1 >= 0 ? p.vol[lo - 1] : -1.0;
      if(up >= dn)
        {
         hi++;
         acc += p.vol[hi];
        }
      else
        {
         lo--;
         acc += p.vol[lo];
        }
     }
   poc = p.base + (pocIdx + 0.5) * p.row;
   vah = p.base + (hi + 1) * p.row;
   val = p.base + lo * p.row;
   return true;
  }

// adds the M1 bars inside [from, to) to the profile
void AddM1(Profile &p, const datetime from, const datetime to)
  {
   MqlRates r[];
   int n = CopyRates(_Symbol, PERIOD_M1, from, to - 1, r);
   if(n < 0)
     {
      if(!gDataMissing)
         PrintFormat("Volume_Profile: M1 data not ready (err %d, M1 bars %d, synced %d, from %s)", GetLastError(),
                     Bars(_Symbol, PERIOD_M1), (int)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_SYNCHRONIZED), TimeToString(from));
      gDataMissing = true; // history is still loading (fresh terminal / iCustom)
      return;
     }
   for(int k = 0; k < n; k++)
      if(r[k].time >= from && r[k].time < to)
         AddBar(p, r[k].high, r[k].low, (double)r[k].tick_volume);
  }

void FinishSession()
  {
   double poc, vah, val;
   if(ValueArea(S, poc, vah, val))
     {
      gPrevOk = true;
      gPPoc = poc;
      gPVah = vah;
      gPVal = val;
      gPrevRange = S.hi - S.lo;
      if(InpHistogram)
         DrawHistogram(S, "prev_");
     }
  }

void DrawHistogram(const Profile &p, const string tag)
  {
   ObjectsDeleteAll(0, gPrefix + tag);
   int n = ArraySize(p.vol);
   if(n == 0)
      return;
   int group = (int)MathCeil(n / 60.0); // at most ~60 drawn rows
   double maxv = 0;
   for(int k = 0; k < n; k += group)
     {
      double v = 0;
      for(int j = k; j < MathMin(n, k + group); j++)
         v += p.vol[j];
      maxv = MathMax(maxv, v);
     }
   if(maxv <= 0)
      return;
   double poc, vah, val;
   ValueArea(p, poc, vah, val);
   long span = (long)(86400 * InpHistWidth / 100);
   for(int k = 0; k < n; k += group)
     {
      double v = 0;
      for(int j = k; j < MathMin(n, k + group); j++)
         v += p.vol[j];
      double y1 = p.base + k * p.row, y2 = p.base + MathMin(n, k + group) * p.row;
      string name = gPrefix + tag + IntegerToString(k);
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, p.day, y1, p.day + (datetime)(span * v / maxv), y2);
      bool inVa = y2 > val && y1 < vah;
      ObjectSetInteger(0, name, OBJPROP_COLOR, inVa ? C'70,110,160' : C'90,90,100');
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
  }

void SetBuffers(const int i, const bool devOk, const double poc, const double vah, const double val)
  {
   bPoc[i] = devOk ? poc : EMPTY_VALUE;
   bVah[i] = devOk ? vah : EMPTY_VALUE;
   bVal[i] = devOk ? val : EMPTY_VALUE;
   bPPoc[i] = gPrevOk ? gPPoc : EMPTY_VALUE;
   bPVah[i] = gPrevOk ? gPVah : EMPTY_VALUE;
   bPVal[i] = gPrevOk ? gPVal : EMPTY_VALUE;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   MqlRates probe[];
   CopyRates(_Symbol, PERIOD_M1, 0, 1, probe); // ask the terminal to start loading M1 history early
   if(PeriodSeconds() > 4 * 3600)
     {
      Print("Volume_Profile: use a timeframe from M1 to H4");
      return INIT_FAILED;
     }
   if(InpVAPercent <= 0 || InpVAPercent > 100 || InpAutoRows < 10)
      return INIT_PARAMETERS_INCORRECT;
   SetIndexBuffer(0, bPoc, INDICATOR_DATA);
   SetIndexBuffer(1, bVah, INDICATOR_DATA);
   SetIndexBuffer(2, bVal, INDICATOR_DATA);
   SetIndexBuffer(3, bPPoc, INDICATOR_DATA);
   SetIndexBuffer(4, bPVah, INDICATOR_DATA);
   SetIndexBuffer(5, bPVal, INDICATOR_DATA);
   for(int k = 0; k < 6; k++)
      PlotIndexSetDouble(k, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, "Volume Profile");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, gPrefix);
  }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[], const double &close[],
                const long &tick_volume[], const long &volume[], const int &spread[])
  {
   if(rates_total < 2)
      return 0;
   int ps = PeriodSeconds();
   gDataMissing = false;
   if(prev_calculated == 0)
     {
      ObjectsDeleteAll(0, gPrefix);
      gLastDone = -1;
      gPrevOk = false;
      gPrevRange = 0;
      S.day = 0;
      S.ok = false;
      ArrayResize(S.vol, 0);
      datetime cutoff = SessionDay(time[rates_total - 1]) - (datetime)InpMaxDays * 86400;
      if(ps != 60)
        {
         // only sessions fully covered by the M1 history the terminal already has; ask for more in the background
         MqlRates probe[];
         CopyRates(_Symbol, PERIOD_M1, cutoff, cutoff + 60, probe);
         datetime m1first = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_FIRSTDATE);
         if(m1first > cutoff)
            cutoff = SessionDay(m1first) + 86400;
        }
      for(int i = 0; i < rates_total; i++)
        {
         SetBuffers(i, false, 0, 0, 0);
         if(time[i] < cutoff)
            gLastDone = i;
        }
      gLastDraw = 0;
     }

   //--- closed bars: commit their M1 volume to the session profile
   for(int i = gLastDone + 1; i <= rates_total - 2; i++)
     {
      datetime day = SessionDay(time[i]);
      if(day != S.day)
        {
         if(S.day != 0)
            FinishSession();
         StartSession(day);
        }
      if(ps == 60)
         AddBar(S, high[i], low[i], (double)tick_volume[i]);
      else
         AddM1(S, time[i], time[i] + ps);
      double poc = 0, vah = 0, val = 0;
      bool ok = ValueArea(S, poc, vah, val);
      SetBuffers(i, ok, poc, vah, val);
      gLastDone = i;
     }

   //--- forming bar: a temporary copy of the profile plus the M1 bars so far
   int f = rates_total - 1;
   datetime fday = SessionDay(time[f]);
   Profile T;
   if(fday == S.day)
     {
      T.day = S.day;
      T.ok = S.ok;
      T.base = S.base;
      T.row = S.row;
      T.hi = S.hi;
      T.lo = S.lo;
      ArrayCopy(T.vol, S.vol);
     }
   else
     {
      if(S.day != 0 && f - 1 == gLastDone)
        {
         FinishSession();
         StartSession(fday);
        }
      T.day = fday;
      T.ok = false;
      T.row = RowHeight();
     }
   if(ps == 60)
      AddBar(T, high[f], low[f], (double)tick_volume[f]);
   else
      AddM1(T, time[f], time[f] + ps);
   double poc = 0, vah = 0, val = 0;
   bool ok = ValueArea(T, poc, vah, val);
   SetBuffers(f, ok, poc, vah, val);
   if(gDataMissing)
      return 0; // some M1 data was not available: recalculate everything on the next call
   if(InpHistogram && TimeCurrent() - gLastDraw >= 5) // redraw at most every 5 seconds
     {
      DrawHistogram(T, "cur_");
      gLastDraw = TimeCurrent();
     }
   return rates_total;
  }
//+------------------------------------------------------------------+
