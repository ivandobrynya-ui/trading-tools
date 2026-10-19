//+------------------------------------------------------------------+
//| SMC_Market_Structure.mq5                                         |
//| MetaTrader 5 port of the Pine Script v6 indicator                |
//| "SMC Market Structure" (same rules, same order of operations)    |
//+------------------------------------------------------------------+
#property copyright   "Ivan Semernya"
#property version     "1.00"
#property description "Swing and internal market structure (BOS / CHoCH), HH / HL / LH / LL,"
#property description "strong / weak highs and lows, equal highs / lows (EQH / EQL)."
#property description "Port of the TradingView version. Signals are calculated on closed bars only."
#property indicator_chart_window
#property indicator_buffers 13
#property indicator_plots   9

#property indicator_label1  "SMC candles"
#property indicator_type1   DRAW_COLOR_CANDLES
#property indicator_color1  C'8,153,129',C'242,54,69'

#property indicator_label2  "Swing bull break (1=BOS, 2=CHoCH)"
#property indicator_type2   DRAW_NONE
#property indicator_label3  "Swing bear break (1=BOS, 2=CHoCH)"
#property indicator_type3   DRAW_NONE
#property indicator_label4  "Internal bull break (1=BOS, 2=CHoCH)"
#property indicator_type4   DRAW_NONE
#property indicator_label5  "Internal bear break (1=BOS, 2=CHoCH)"
#property indicator_type5   DRAW_NONE
#property indicator_label6  "EQH"
#property indicator_type6   DRAW_NONE
#property indicator_label7  "EQL"
#property indicator_type7   DRAW_NONE
#property indicator_label8  "Swing high (1=H, 2=HH, 3=LH)"
#property indicator_type8   DRAW_NONE
#property indicator_label9  "Swing low (1=L, 2=LL, 3=HL)"
#property indicator_type9   DRAW_NONE

//--- pivot tie rules of TradingView's ta.pivothigh / ta.pivotlow, confirmed bar by bar on 10,613 bars:
//--- on the left only a strictly higher (lower) bar cancels the pivot, on the right an equal one cancels it too
//--- true  = an equal neighbour cancels the pivot, false = only a strictly higher (lower) one does
#define PIVOT_LEFT_TIE_CANCELS   false
#define PIVOT_RIGHT_TIE_CANCELS  true

enum ENUM_BREAK_SRC
  {
   BREAK_CLOSE = 0, // Close
   BREAK_WICK  = 1  // Wick
  };

//--- inputs (same names and defaults as the Pine version)
input group "Swing structure"
input bool InpShowSwing = true;   // Show swing BOS / CHoCH
input int  InpSwingLen  = 25;     // Swing pivot length
input bool InpShowHHLL  = true;   // Label swing points (HH / HL / LH / LL)

input group "Internal structure"
input bool InpShowInt = true;     // Show internal BOS / CHoCH
input int  InpIntLen  = 5;        // Internal pivot length

input group "Confirmation"
input ENUM_BREAK_SRC InpBreakSrc = BREAK_CLOSE; // Break confirmed by

input group "Levels"
input bool   InpShowStrong = true; // Strong / weak high & low
input bool   InpShowEq     = true; // Equal highs / lows (EQH / EQL)
input int    InpEqLen      = 3;    // EQH / EQL pivot length
input double InpEqThresh   = 0.1;  // EQH / EQL threshold (x ATR 200)

input group "Style"
input color           InpBullCol      = C'8,153,129';  // Bullish
input color           InpBearCol      = C'242,54,69';  // Bearish
input ENUM_LINE_STYLE InpIntLineStyle = STYLE_DASH;    // Internal line style
input int             InpSwingFont    = 8;             // Swing label font size
input bool            InpColorBars    = false;         // Color candles by internal trend

input group "Display"
input int InpMaxInt    = 20;   // Max internal breaks on chart
input int InpMaxSwing  = 50;   // Max swing breaks on chart
input int InpMaxLabels = 300;  // Max HH/LL and EQH/EQL objects on chart

input group "Alerts (on bar close)"
input bool InpAlertSwing    = false; // Alert on swing BOS / CHoCH
input bool InpAlertInternal = false; // Alert on internal BOS / CHoCH
input bool InpAlertEq       = false; // Alert on EQH / EQL
input bool InpAlertPush     = false; // Also send push notifications

//--- buffers
double bOpen[], bHigh[], bLow[], bClose[], bColor[];
double eSwBull[], eSwBear[], eIntBull[], eIntBear[], eEqh[], eEql[], ePivH[], ePivL[];

//--- state (mirrors the Pine UDTs)
struct Pivot
  {
   bool              ok;       // false = na
   double            price;
   int               bar;
   bool              crossed;
  };

struct Structure
  {
   Pivot             hi;
   Pivot             lo;
   int               trend;    // 1 bullish, -1 bearish, 0 undefined
  };

Structure gSwing, gInt;
bool   gTopOk, gBotOk;
double gTrailTop, gTrailBot;
int    gTrailTopBar, gTrailBotBar;
bool   gEqHOk, gEqLOk;
double gLastEqH, gLastEqL;
int    gLastEqHBar, gLastEqLBar;
double gAtr, gTrSum;
int    gTrCount;
bool   gAtrOk;
int    gLastDone = -1;     // last closed bar already processed
bool   gLive     = false;  // true after the first full history pass (alerts only then)
string gPrefix;
long   gObjId = 0;
string gIntObjs[], gSwObjs[], gLblObjs[];

//+------------------------------------------------------------------+
//| Pivots: same as ta.pivothigh(src, len, len) / ta.pivotlow        |
//+------------------------------------------------------------------+
bool PivotHigh(const double &src[], const int i, const int len, double &val)
  {
   int p = i - len;
   if(p - len < 0)
      return false;
   double v = src[p];
   for(int k = 1; k <= len; k++)
     {
      double x = src[p - k];
      if(PIVOT_LEFT_TIE_CANCELS ? x >= v : x > v)
         return false;
     }
   for(int k = 1; k <= len; k++)
     {
      double x = src[p + k];
      if(PIVOT_RIGHT_TIE_CANCELS ? x >= v : x > v)
         return false;
     }
   val = v;
   return true;
  }

bool PivotLow(const double &src[], const int i, const int len, double &val)
  {
   int p = i - len;
   if(p - len < 0)
      return false;
   double v = src[p];
   for(int k = 1; k <= len; k++)
     {
      double x = src[p - k];
      if(PIVOT_LEFT_TIE_CANCELS ? x <= v : x < v)
         return false;
     }
   for(int k = 1; k <= len; k++)
     {
      double x = src[p + k];
      if(PIVOT_RIGHT_TIE_CANCELS ? x <= v : x < v)
         return false;
     }
   val = v;
   return true;
  }

//+------------------------------------------------------------------+
//| Chart objects                                                    |
//+------------------------------------------------------------------+
string NewBase(const string kind)
  {
   gObjId++;
   return gPrefix + kind + IntegerToString(gObjId);
  }

void MakeLine(const string name, const datetime t1, const double p1, const datetime t2, const double p2,
              const color c, const int width, const ENUM_LINE_STYLE style)
  {
   if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2))
      return;
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void MakeText(const string name, const datetime t, const double p, const string txt, const color c,
              const int size, const ENUM_ANCHOR_POINT anchor)
  {
   if(!ObjectCreate(0, name, OBJ_TEXT, 0, t, p))
      return;
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void DeleteBase(const string base)
  {
   ObjectDelete(0, base + "_l");
   ObjectDelete(0, base + "_t");
  }

// keeps only the last maxN objects of one kind, like the Pine version's arrays of lines and labels
void PushCapped(string &queue[], const string base, const int maxN)
  {
   int n = ArraySize(queue);
   ArrayResize(queue, n + 1, 64);
   queue[n] = base;
   while(ArraySize(queue) > MathMax(maxN, 1))
     {
      DeleteBase(queue[0]);
      ArrayRemove(queue, 0, 1);
     }
  }

void DrawBreak(const int x1, const double y, const int code, const color c, const bool internal,
               const bool isUp, const int i, const datetime &time[])
  {
   string base = NewBase(internal ? "i" : "s");
   MakeLine(base + "_l", time[x1], y, time[i], y, c, internal ? 1 : 2, internal ? InpIntLineStyle : STYLE_SOLID);
   MakeText(base + "_t", time[(x1 + i) / 2], y, code == 2 ? "CHoCH" : "BOS", c, internal ? 7 : InpSwingFont,
            isUp ? ANCHOR_LOWER : ANCHOR_UPPER);
   if(internal)
      PushCapped(gIntObjs, base, InpMaxInt);
   else
      PushCapped(gSwObjs, base, InpMaxSwing);
  }

void Notify(const string msg)
  {
   Alert(msg);
   if(InpAlertPush)
      SendNotification(msg);
  }

//+------------------------------------------------------------------+
//| Break checks: same as the Pine method checkBreaks()              |
//+------------------------------------------------------------------+
void CheckBreaks(Structure &s, const bool internal, const bool skipHiOk, const double skipHi,
                 const bool skipLoOk, const double skipLo, const bool show, const int i,
                 const datetime &time[], const double &high[], const double &low[], const double &close[],
                 int &bullCode, int &bearCode)
  {
   bullCode = 0;
   bearCode = 0;
   double upSrc = InpBreakSrc == BREAK_CLOSE ? close[i] : high[i];
   double dnSrc = InpBreakSrc == BREAK_CLOSE ? close[i] : low[i];

   if(s.hi.ok && !s.hi.crossed && upSrc > s.hi.price)
     {
      if(!(internal && skipHiOk && s.hi.price == skipHi))
        {
         bullCode = s.trend == -1 ? 2 : 1;
         if(show)
            DrawBreak(s.hi.bar, s.hi.price, bullCode, InpBullCol, internal, true, i, time);
        }
      s.hi.crossed = true;
      s.trend = 1;
     }
   if(s.lo.ok && !s.lo.crossed && dnSrc < s.lo.price)
     {
      if(!(internal && skipLoOk && s.lo.price == skipLo))
        {
         bearCode = s.trend == 1 ? 2 : 1;
         if(show)
            DrawBreak(s.lo.bar, s.lo.price, bearCode, InpBearCol, internal, false, i, time);
        }
      s.lo.crossed = true;
      s.trend = -1;
     }
  }

//+------------------------------------------------------------------+
//| One closed bar, in the exact order of the Pine script            |
//+------------------------------------------------------------------+
void ProcessBar(const int i, const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[])
  {
   //--- ATR(200) = RMA of true range, seeded with an SMA (ta.atr)
   double tr = i == 0 ? high[i] - low[i]
               : MathMax(high[i] - low[i], MathMax(MathAbs(high[i] - close[i - 1]), MathAbs(low[i] - close[i - 1])));
   if(!gAtrOk)
     {
      gTrSum += tr;
      gTrCount++;
      if(gTrCount == 200)
        {
         gAtr   = gTrSum / 200.0;
         gAtrOk = true;
        }
     }
   else
      gAtr = (1.0 / 200.0) * tr + (1.0 - 1.0 / 200.0) * gAtr; // same arithmetic as Pine's ta.rma

   eSwBull[i] = 0;
   eSwBear[i] = 0;
   eIntBull[i] = 0;
   eIntBear[i] = 0;
   eEqh[i] = 0;
   eEql[i] = 0;
   ePivH[i] = 0;
   ePivL[i] = 0;

   //--- pivots (confirmed N bars later: no repainting)
   double sph = 0, spl = 0, iph = 0, ipl = 0, eph = 0, epl = 0;
   bool hasSph = PivotHigh(high, i, InpSwingLen, sph);
   bool hasSpl = PivotLow(low, i, InpSwingLen, spl);
   bool hasIph = PivotHigh(high, i, InpIntLen, iph);
   bool hasIpl = PivotLow(low, i, InpIntLen, ipl);
   bool hasEph = PivotHigh(high, i, InpEqLen, eph);
   bool hasEpl = PivotLow(low, i, InpEqLen, epl);

   if(hasSph)
     {
      int pb = i - InpSwingLen;
      int code = !gSwing.hi.ok ? 1 : (sph > gSwing.hi.price ? 2 : 3);
      ePivH[i] = code;
      if(InpShowHHLL)
        {
         string base = NewBase("h");
         MakeText(base + "_t", time[pb], sph, code == 1 ? "H" : code == 2 ? "HH" : "LH", InpBearCol, 7, ANCHOR_LOWER);
         PushCapped(gLblObjs, base, InpMaxLabels);
        }
      gSwing.hi.ok = true;
      gSwing.hi.price = sph;
      gSwing.hi.bar = pb;
      gSwing.hi.crossed = false;
      gTopOk = true;
      gTrailTop = sph;
      gTrailTopBar = pb;
     }

   if(hasSpl)
     {
      int pb = i - InpSwingLen;
      int code = !gSwing.lo.ok ? 1 : (spl < gSwing.lo.price ? 2 : 3);
      ePivL[i] = code;
      if(InpShowHHLL)
        {
         string base = NewBase("h");
         MakeText(base + "_t", time[pb], spl, code == 1 ? "L" : code == 2 ? "LL" : "HL", InpBullCol, 7, ANCHOR_UPPER);
         PushCapped(gLblObjs, base, InpMaxLabels);
        }
      gSwing.lo.ok = true;
      gSwing.lo.price = spl;
      gSwing.lo.bar = pb;
      gSwing.lo.crossed = false;
      gBotOk = true;
      gTrailBot = spl;
      gTrailBotBar = pb;
     }

   if(hasIph)
     {
      gInt.hi.ok = true;
      gInt.hi.price = iph;
      gInt.hi.bar = i - InpIntLen;
      gInt.hi.crossed = false;
     }
   if(hasIpl)
     {
      gInt.lo.ok = true;
      gInt.lo.price = ipl;
      gInt.lo.bar = i - InpIntLen;
      gInt.lo.crossed = false;
     }

   //--- trailing extremes since the last swing pivot
   if(!gTopOk || high[i] > gTrailTop)
     {
      gTopOk = true;
      gTrailTop = high[i];
      gTrailTopBar = i;
     }
   if(!gBotOk || low[i] < gTrailBot)
     {
      gBotOk = true;
      gTrailBot = low[i];
      gTrailBotBar = i;
     }

   //--- breaks of structure: swing first, then internal (internal skips levels shared with swing)
   int sBull, sBear, iBull, iBear;
   CheckBreaks(gSwing, false, false, 0, false, 0, InpShowSwing, i, time, high, low, close, sBull, sBear);
   CheckBreaks(gInt, true, gSwing.hi.ok, gSwing.hi.price, gSwing.lo.ok, gSwing.lo.price, InpShowInt, i, time, high, low, close, iBull, iBear);
   eSwBull[i] = sBull;
   eSwBear[i] = sBear;
   eIntBull[i] = iBull;
   eIntBear[i] = iBear;

   //--- equal highs / lows
   bool eqhNow = false, eqlNow = false;
   if(hasEph)
     {
      int pb = i - InpEqLen;
      if(gEqHOk && gAtrOk && MathAbs(eph - gLastEqH) < InpEqThresh * gAtr)
        {
         eqhNow = true;
         if(InpShowEq)
           {
            string base = NewBase("e");
            MakeLine(base + "_l", time[gLastEqHBar], gLastEqH, time[pb], eph, InpBearCol, 1, STYLE_DOT);
            MakeText(base + "_t", time[(gLastEqHBar + pb) / 2], MathMax(eph, gLastEqH), "EQH", InpBearCol, 7, ANCHOR_LOWER);
            PushCapped(gLblObjs, base, InpMaxLabels);
           }
        }
      gEqHOk = true;
      gLastEqH = eph;
      gLastEqHBar = pb;
     }
   if(hasEpl)
     {
      int pb = i - InpEqLen;
      if(gEqLOk && gAtrOk && MathAbs(epl - gLastEqL) < InpEqThresh * gAtr)
        {
         eqlNow = true;
         if(InpShowEq)
           {
            string base = NewBase("e");
            MakeLine(base + "_l", time[gLastEqLBar], gLastEqL, time[pb], epl, InpBullCol, 1, STYLE_DOT);
            MakeText(base + "_t", time[(gLastEqLBar + pb) / 2], MathMin(epl, gLastEqL), "EQL", InpBullCol, 7, ANCHOR_UPPER);
            PushCapped(gLblObjs, base, InpMaxLabels);
           }
        }
      gEqLOk = true;
      gLastEqL = epl;
      gLastEqLBar = pb;
     }
   eEqh[i] = eqhNow ? 1 : 0;
   eEql[i] = eqlNow ? 1 : 0;

   //--- candle colors by internal trend
   if(InpColorBars && gInt.trend != 0)
     {
      bOpen[i] = open[i];
      bHigh[i] = high[i];
      bLow[i] = low[i];
      bClose[i] = close[i];
      bColor[i] = gInt.trend == 1 ? 0 : 1;
     }
   else
     {
      bOpen[i] = EMPTY_VALUE;
      bHigh[i] = EMPTY_VALUE;
      bLow[i] = EMPTY_VALUE;
      bClose[i] = EMPTY_VALUE;
      bColor[i] = 0;
     }

   //--- alerts only for bars that close while the indicator is running
   if(gLive)
     {
      string tf = StringSubstr(EnumToString(Period()), 7);
      string head = _Symbol + " " + tf + ": ";
      if(InpAlertSwing && sBull > 0)
         Notify(head + "swing bullish " + (sBull == 2 ? "CHoCH" : "BOS"));
      if(InpAlertSwing && sBear > 0)
         Notify(head + "swing bearish " + (sBear == 2 ? "CHoCH" : "BOS"));
      if(InpAlertInternal && iBull > 0)
         Notify(head + "internal bullish " + (iBull == 2 ? "CHoCH" : "BOS"));
      if(InpAlertInternal && iBear > 0)
         Notify(head + "internal bearish " + (iBear == 2 ? "CHoCH" : "BOS"));
      if(InpAlertEq && eqhNow)
         Notify(head + "equal highs (EQH)");
      if(InpAlertEq && eqlNow)
         Notify(head + "equal lows (EQL)");
     }
  }

//+------------------------------------------------------------------+
//| Strong / weak high & low, redrawn after each closed bar           |
//+------------------------------------------------------------------+
void DrawStrongWeak(const int last, const datetime &time[])
  {
   string names[4] = {"sw_top_l", "sw_top_t", "sw_bot_l", "sw_bot_t"};
   for(int k = 0; k < 4; k++)
      ObjectDelete(0, gPrefix + names[k]);
   if(!InpShowStrong || last < 0 || !gTopOk || !gBotOk)
      return;
   datetime xr = time[last] + 20 * PeriodSeconds();
   MakeLine(gPrefix + "sw_top_l", time[gTrailTopBar], gTrailTop, xr, gTrailTop, InpBearCol, 1, STYLE_SOLID);
   MakeText(gPrefix + "sw_top_t", xr, gTrailTop, gSwing.trend == -1 ? "Strong High" : "Weak High", InpBearCol, 8, ANCHOR_LEFT);
   MakeLine(gPrefix + "sw_bot_l", time[gTrailBotBar], gTrailBot, xr, gTrailBot, InpBullCol, 1, STYLE_SOLID);
   MakeText(gPrefix + "sw_bot_t", xr, gTrailBot, gSwing.trend == 1 ? "Strong Low" : "Weak Low", InpBullCol, 8, ANCHOR_LEFT);
  }

void ResetState()
  {
   ObjectsDeleteAll(0, gPrefix);
   ZeroMemory(gSwing);
   ZeroMemory(gInt);
   gTopOk = gBotOk = false;
   gEqHOk = gEqLOk = false;
   gAtr = gTrSum = 0;
   gTrCount = 0;
   gAtrOk = false;
   gLastDone = -1;
   ArrayResize(gIntObjs, 0);
   ArrayResize(gSwObjs, 0);
   ArrayResize(gLblObjs, 0);
  }

//+------------------------------------------------------------------+
//| Standard handlers                                                |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpSwingLen < 1 || InpIntLen < 1 || InpEqLen < 1)
      return INIT_PARAMETERS_INCORRECT;

   SetIndexBuffer(0, bOpen, INDICATOR_DATA);
   SetIndexBuffer(1, bHigh, INDICATOR_DATA);
   SetIndexBuffer(2, bLow, INDICATOR_DATA);
   SetIndexBuffer(3, bClose, INDICATOR_DATA);
   SetIndexBuffer(4, bColor, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(5, eSwBull, INDICATOR_DATA);
   SetIndexBuffer(6, eSwBear, INDICATOR_DATA);
   SetIndexBuffer(7, eIntBull, INDICATOR_DATA);
   SetIndexBuffer(8, eIntBear, INDICATOR_DATA);
   SetIndexBuffer(9, eEqh, INDICATOR_DATA);
   SetIndexBuffer(10, eEql, INDICATOR_DATA);
   SetIndexBuffer(11, ePivH, INDICATOR_DATA);
   SetIndexBuffer(12, ePivL, INDICATOR_DATA);

   // keep DRAW_COLOR_CANDLES even when coloring is off (buffers stay empty): switching the plot
   // to DRAW_NONE would change the buffer-to-plot mapping and hide the signal buffers from iCustom
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 0, InpBullCol);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 1, InpBearCol);
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   for(int p = 1; p < 9; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   gPrefix = StringFormat("SMC%I64d_", ChartID() % 100000 + MathRand());
   IndicatorSetString(INDICATOR_SHORTNAME, "SMC Structure");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   ResetState();
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
   if(prev_calculated == 0)
     {
      ResetState();
      gLive = false;
     }

   int lastClosed = rates_total - 2;
   bool advanced = false;
   for(int i = gLastDone + 1; i <= lastClosed; i++)
     {
      ProcessBar(i, time, open, high, low, close);
      gLastDone = i;
      advanced = true;
     }

   //--- the forming bar carries no signals (everything is decided on bar close)
   int f = rates_total - 1;
   eSwBull[f] = eSwBear[f] = eIntBull[f] = eIntBear[f] = 0;
   eEqh[f] = eEql[f] = ePivH[f] = ePivL[f] = 0;
   if(InpColorBars && gInt.trend != 0)
     {
      bOpen[f] = open[f];
      bHigh[f] = high[f];
      bLow[f] = low[f];
      bClose[f] = close[f];
      bColor[f] = gInt.trend == 1 ? 0 : 1;
     }
   else
     {
      bOpen[f] = bHigh[f] = bLow[f] = bClose[f] = EMPTY_VALUE;
      bColor[f] = 0;
     }

   if(advanced)
      DrawStrongWeak(lastClosed, time);
   gLive = true;
   return rates_total;
  }
//+------------------------------------------------------------------+
