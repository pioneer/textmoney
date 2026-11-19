import 'dart:math' as math;

import 'package:flutter/rendering.dart';
import 'package:flutter/widgets.dart';

class CategoryMenuBounds extends StatefulWidget {
  const CategoryMenuBounds({
    super.key,
    required this.heightLimit,
    required this.builder,
  });

  final double heightLimit;
  final Widget Function(
    BuildContext context,
    double menuHeight,
    double menuWidth,
    Alignment alignment,
  )
  builder;

  @override
  State<CategoryMenuBounds> createState() => _CategoryMenuBoundsState();
}

class _CategoryMenuBoundsState extends State<CategoryMenuBounds>
    with WidgetsBindingObserver {
  final _fieldKey = GlobalKey();
  Offset _position = Offset.zero;
  double _height = 56;
  double _width = 1;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeMetrics() => setState(() {});

  void _measure() {
    if (!mounted) return;
    final box = _fieldKey.currentContext?.findRenderObject();
    if (box is! RenderBox ||
        !box.hasSize ||
        box.size.height <= 0 ||
        box.size.width <= 0) {
      return;
    }
    final position = box.localToGlobal(Offset.zero);
    if (position != _position ||
        box.size.height != _height ||
        box.size.width != _width) {
      setState(() {
        _position = position;
        _height = box.size.height;
        _width = box.size.width;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // Dialog removes keyboard insets from its content's MediaQuery. An overlay
    // must use the actual view, not that already-consumed dialog geometry.
    final viewport = MediaQueryData.fromView(View.of(context));
    final top = math.max(viewport.viewPadding.top, viewport.viewInsets.top);
    final bottom =
        viewport.size.height -
        math.max(viewport.viewPadding.bottom, viewport.viewInsets.bottom);
    final available = math.max(0.0, bottom - top);
    final menuTop = top + available * 0.2;
    final left = math.max(viewport.viewPadding.left, viewport.viewInsets.left);
    final right =
        viewport.size.width -
        math.max(viewport.viewPadding.right, viewport.viewInsets.right);
    final menuLeft = left + 16;
    final menuWidth = math.max(0.0, right - left - 32);
    return Listener(
      key: _fieldKey,
      onPointerDown: (_) => _measure(),
      child: _MeasureAfterPaint(
        onPaint: _measure,
        child: widget.builder(
          context,
          math.min(widget.heightLimit, available * 0.8),
          menuWidth,
          Alignment(
            -1 + 2 * (menuLeft - _position.dx) / _width,
            -1 + 2 * (menuTop - _position.dy) / _height,
          ),
        ),
      ),
    );
  }
}

class _MeasureAfterPaint extends SingleChildRenderObjectWidget {
  const _MeasureAfterPaint({required this.onPaint, required super.child});

  final VoidCallback onPaint;

  @override
  RenderObject createRenderObject(BuildContext context) => _MeasureBox(onPaint);

  @override
  void updateRenderObject(BuildContext context, _MeasureBox renderObject) {
    renderObject.onPaint = onPaint;
  }
}

class _MeasureBox extends RenderProxyBox {
  _MeasureBox(this.onPaint);

  VoidCallback onPaint;

  @override
  void paint(PaintingContext context, Offset offset) {
    super.paint(context, offset);
    WidgetsBinding.instance.addPostFrameCallback((_) => onPaint());
  }
}
