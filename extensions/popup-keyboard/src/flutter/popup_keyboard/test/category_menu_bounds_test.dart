import 'package:flet/flet.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:popup_keyboard/src/extension.dart';
import 'package:provider/provider.dart';

void main() {
  for (final size in [const Size(390, 844), const Size(844, 390)]) {
    for (final keyboard in [0.0, 180.0]) {
      for (final count in [3, 30]) {
        testWidgets(
          'category menu bounds $size keyboard=$keyboard rows=$count',
          (tester) async {
            tester.view.devicePixelRatio = 1;
            tester.view.physicalSize = size;
            tester.view.viewPadding = const FakeViewPadding(
              top: 24,
              bottom: 24,
              left: 12,
              right: 24,
            );
            tester.view.padding = FakeViewPadding(
              top: 24,
              bottom: keyboard > 0 ? 0 : 24,
            );
            tester.view.viewInsets = FakeViewPadding(bottom: keyboard);
            addTearDown(tester.view.reset);
            const menuColor = Color(0xff223344);
            final backend = FletBackend(
              pageUri: Uri(),
              assetsDir: '',
              multiView: false,
              extensions: [Extension()],
            );
            addTearDown(backend.dispose);
            final control = Control.fromMap(
              {
                '_c': 'MobileCategoryMenu',
                '_i': 3,
                'menu_height_limit': 672,
                'content': {
                  '_c': 'Dropdown',
                  '_i': 4,
                  'menu_height': 672,
                  'width': count == 3 ? 180 : 260,
                  'menu_style': {
                    'padding': {'top': 8, 'bottom': 8, 'left': 0, 'right': 0},
                    'bgcolor': '#223344',
                  },
                  'options': [
                    for (var index = 0; index < count; index++)
                      {
                        '_c': 'DropdownOption',
                        '_i': 5 + index,
                        'key': '$index',
                        'text': index == 0 && count == 30
                            ? 'Category with a long display name (Expenses:Food:Shops:Market)'
                            : 'Category $index',
                      },
                  ],
                },
              },
              backend,
              parent: backend.page,
            );
            await tester.pumpWidget(
              ChangeNotifierProvider.value(
                value: backend,
                child: MaterialApp(
                  home: Builder(
                    builder: (context) => TextButton(
                      onPressed: () => showDialog<void>(
                        context: context,
                        builder: (context) => AlertDialog(
                          insetPadding: EdgeInsets.zero,
                          content: SizedBox(
                            width: double.infinity,
                            height: double.infinity,
                            child: Align(
                              alignment: Alignment.topLeft,
                              child: Padding(
                                padding: EdgeInsets.only(
                                  top: size.height > 400 ? 110 : 0,
                                ),
                                child: ControlWidget(control: control),
                              ),
                            ),
                          ),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.pop(context),
                              child: const Text('Cancel'),
                            ),
                          ],
                        ),
                      ),
                      child: const Text('Edit'),
                    ),
                  ),
                ),
              ),
            );
            await tester.tap(find.text('Edit'));
            await tester.pumpAndSettle();
            await tester.tap(find.byType(DropdownMenu<String>));
            await tester.pumpAndSettle();
            final panel = find.byWidgetPredicate(
              (widget) => widget is Material && widget.color == menuColor,
            );
            expect(panel, findsOneWidget);
            final rect = tester.getRect(panel);
            expect(rect.left, closeTo(12 + 16, 0.01));
            expect(rect.right, closeTo(size.width - 24 - 16, 0.01));
            final bottom = size.height - (keyboard > 24 ? keyboard : 24);
            final floor = 24 + (bottom - 24) * 0.2;
            expect(
              tester
                  .widget<DropdownMenu<String>>(
                    find.byType(DropdownMenu<String>),
                  )
                  .menuHeight,
              closeTo((bottom - 24) * 0.8, 0.01),
            );
            expect(rect.top, greaterThanOrEqualTo(floor - 0.01));
            expect(rect.bottom, lessThanOrEqualTo(bottom + 0.01));
            if (count == 30) {
              expect(rect.top, closeTo(floor, 0.01));
              await tester.drag(panel, const Offset(0, -1800));
              await tester.pumpAndSettle();
              await tester.ensureVisible(find.text('Category 29').last);
              await tester.tap(find.text('Category 29').last);
              await tester.pumpAndSettle();
              expect(control.child('content')!.getString('value'), '29');
            } else {
              await tester.sendKeyEvent(LogicalKeyboardKey.escape);
              await tester.pumpAndSettle();
            }
            expect(find.text('Cancel'), findsOneWidget);
            expect(panel, findsNothing);
            expect(tester.takeException(), isNull);
          },
        );
      }
    }
  }
}
