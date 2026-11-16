import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:popup_keyboard/src/popup_keyboard_scope.dart';

void main() {
  for (final child in ['date', 'icon']) {
    for (final dismissal in ['escape', 'cancel', 'save']) {
      testWidgets('popup Escape survives $child dialog $dismissal', (
        tester,
      ) async {
        var cancelled = 0;
        final parentListener = FocusNode();
        final inputFocus = FocusNode();
        final input = TextEditingController(text: 'Unsaved category name');
        addTearDown(parentListener.dispose);
        addTearDown(inputFocus.dispose);
        addTearDown(input.dispose);
        await tester.pumpWidget(
          MaterialApp(
            home: Builder(
              builder: (context) => TextButton(
                onPressed: () => showDialog<void>(
                  context: context,
                  barrierDismissible: false,
                  builder: (context) => AlertDialog(
                    scrollable: true,
                    content: PopupKeyboardScope(
                      onEscape: () => cancelled++,
                      restoreFocus: true,
                      child: KeyboardListener(
                        focusNode: parentListener,
                        onKeyEvent: (event) {
                          if (event is KeyDownEvent &&
                              event.logicalKey == LogicalKeyboardKey.escape) {
                            cancelled++;
                          }
                        },
                        child: Column(
                          children: [
                            TextField(
                              controller: input,
                              focusNode: inputFocus,
                              autofocus: true,
                            ),
                            IconButton(
                              key: const ValueKey('open-child'),
                              icon: const Icon(Icons.calendar_today),
                              onPressed: () => showDialog<void>(
                                context: context,
                                builder: (childContext) => child == 'date'
                                    ? DatePickerDialog(
                                        firstDate: DateTime(1970),
                                        lastDate: DateTime(2100),
                                      )
                                    : AlertDialog(
                                        content: TextField(autofocus: true),
                                        actions: [
                                          TextButton(
                                            onPressed: () =>
                                                Navigator.pop(childContext),
                                            child: const Text('Cancel'),
                                          ),
                                          TextButton(
                                            onPressed: () =>
                                                Navigator.pop(childContext),
                                            child: const Text('Save'),
                                          ),
                                        ],
                                      ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
                child: const Text('Edit category'),
              ),
            ),
          ),
        );
        await tester.tap(find.text('Edit category'));
        await tester.pumpAndSettle();
        expect(inputFocus.hasPrimaryFocus, isTrue);
        await tester.tap(
          find.byKey(const ValueKey('open-child')),
          kind: PointerDeviceKind.mouse,
        );
        await tester.pumpAndSettle();
        if (dismissal == 'escape') {
          await tester.sendKeyEvent(LogicalKeyboardKey.escape);
        } else {
          await tester.tap(
            find.text(
              dismissal == 'cancel'
                  ? 'Cancel'
                  : child == 'date'
                  ? 'OK'
                  : 'Save',
            ),
            kind: PointerDeviceKind.mouse,
          );
        }
        await tester.pumpAndSettle();
        expect(cancelled, 0);
        expect(input.text, 'Unsaved category name');
        await tester.sendKeyEvent(LogicalKeyboardKey.escape);
        await tester.pumpAndSettle();
        expect(cancelled, 1);
      });
    }
  }

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
