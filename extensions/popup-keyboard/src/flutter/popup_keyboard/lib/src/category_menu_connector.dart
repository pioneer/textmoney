import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:material_ui/material_ui.dart' show Theme;

class CategoryMenuGuides extends StatelessWidget {
  const CategoryMenuGuides({
    super.key,
    required this.continued,
    required this.child,
  });

  final List<bool> continued;
  final Widget? child;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: continued.isEmpty
          ? null
          : _GuidesPainter(
              continued,
              Theme.of(context).colorScheme.outlineVariant,
            ),
      child: child,
    );
  }
}

class _GuidesPainter extends CustomPainter {
  const _GuidesPainter(this.continued, this.color);

  final List<bool> continued;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.25
      ..style = PaintingStyle.stroke;
    final center = size.height / 2;
    for (var level = 0; level < continued.length; level++) {
      final x = 16.0 * level + 4;
      final last = level == continued.length - 1;
      if (continued[level]) {
        canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
        if (last) {
          canvas.drawLine(Offset(x, center), Offset(x + 11, center), paint);
        }
      } else if (last) {
        final radius = math.min(3.0, center);
        final elbow = Path()
          ..moveTo(x, 0)
          ..lineTo(x, center - radius)
          ..quadraticBezierTo(x, center, x + radius, center)
          ..lineTo(x + 11, center);
        canvas.drawPath(elbow, paint);
      }
    }
  }

  @override
  bool shouldRepaint(_GuidesPainter oldDelegate) =>
      color != oldDelegate.color ||
      !listEquals(continued, oldDelegate.continued);
}
