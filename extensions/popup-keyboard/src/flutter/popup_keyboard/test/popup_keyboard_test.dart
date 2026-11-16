import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:popup_keyboard/src/popup_keyboard_scope.dart';

void main() {
  for (final dismissal in ['escape', 'pointer', 'keyboard', 'outside']) {
    testWidgets('popup Escape survives dropdown $dismissal', (tester) async {
      var cancelled = 0;
      var parentCancelled = 0;
      var submitted = 0;
      String? selected;
      final focus = FocusNode(canRequestFocus: false);
      final listener = FocusNode();
      addTearDown(focus.dispose);
      addTearDown(listener.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PopupKeyboardScope(
              onEscape: () => parentCancelled++,
              child: PopupKeyboardScope(
                onEscape: () => cancelled++,
                child: KeyboardListener(
                  focusNode: listener,
                  autofocus: true,
                  onKeyEvent: (event) {
                    if (event is KeyDownEvent) {
                      if (event.logicalKey == LogicalKeyboardKey.escape) {
                        cancelled++;
                      } else if (event.logicalKey == LogicalKeyboardKey.enter) {
                        submitted++;
                      }
                    }
                  },
                  child: Column(
                    children: [
                      DropdownMenu<String>(
                        focusNode: focus,
                        onSelected: (value) => selected = value,
                        dropdownMenuEntries: const [
                          DropdownMenuEntry(value: 'food', label: 'Food'),
                        ],
                      ),
                      const SizedBox(height: 200, width: 200),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.byType(DropdownMenu<String>));
      await tester.pumpAndSettle();
      switch (dismissal) {
        case 'escape':
          await tester.sendKeyEvent(LogicalKeyboardKey.escape);
        case 'pointer':
          await tester.tap(find.text('Food').last);
        case 'keyboard':
          await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
          await tester.pump();
          await tester.sendKeyEvent(LogicalKeyboardKey.enter);
        case 'outside':
          await tester.tapAt(const Offset(400, 400));
      }
      await tester.pumpAndSettle();
      expect(cancelled, 0);
      expect(submitted, 0);
      if (dismissal == 'pointer' || dismissal == 'keyboard') {
        expect(selected, 'food');
      }
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();
      expect(cancelled, 1);
      expect(parentCancelled, 0);
      expect(submitted, 0);
    });
  }
}
