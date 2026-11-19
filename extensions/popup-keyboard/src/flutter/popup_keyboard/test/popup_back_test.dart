import 'package:flet/flet.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:popup_keyboard/src/extension.dart';
import 'package:popup_keyboard/src/popup_menu_back_scope.dart';
import 'package:provider/provider.dart';

void main() {
  for (final wrapped in [false, true]) {
    testWidgets(
      'Android Back closes ${wrapped ? "wrapped" : "ordinary"} menu first',
      (tester) async {
        final backend = FletBackend(
          pageUri: Uri(),
          assetsDir: '',
          multiView: false,
          extensions: [Extension()],
        );
        addTearDown(backend.dispose);
        final dropdown = {
          '_c': 'Dropdown',
          '_i': 4,
          'width': 260,
          'options': [
            {'_c': 'DropdownOption', '_i': 5, 'key': 'food', 'text': 'Food'},
          ],
        };
        final field = wrapped
            ? {'_c': 'MobileCategoryMenu', '_i': 3, 'content': dropdown}
            : dropdown;
        final control = Control.fromMap(
          {'_c': 'PopupMenuBackScope', '_i': 2, 'content': field},
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
                      content: ControlWidget(control: control),
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
        final anchor = tester.widget<MenuAnchor>(
          find.descendant(
            of: find.byType(DropdownMenu<String>),
            matching: find.byType(MenuAnchor),
          ),
        );
        expect(anchor.controller!.isOpen, isTrue);

        await tester.binding.handlePopRoute();
        await tester.pumpAndSettle();
        expect(find.text('Cancel'), findsOneWidget);
        expect(anchor.controller!.isOpen, isFalse);
        expect(
          (wrapped
                  ? control.child('content')!.child('content')
                  : control.child('content'))!
              .getString('value'),
          isNull,
        );

        await tester.binding.handlePopRoute();
        await tester.pumpAndSettle();
        expect(find.text('Cancel'), findsNothing);
      },
    );
  }

  testWidgets('Flet dialog remains open after Back closes its dropdown', (
    tester,
  ) async {
    final backend = FletBackend(
      pageUri: Uri(),
      assetsDir: '',
      multiView: false,
      extensions: [Extension()],
    );
    addTearDown(backend.dispose);
    final dialog = Control.fromMap(
      {
        '_c': 'AlertDialog',
        '_i': 10,
        'open': true,
        'modal': true,
        'title': 'Flet editor',
        'content': {
          '_c': 'PopupMenuBackScope',
          '_i': 11,
          'content': {
            '_c': 'Dropdown',
            '_i': 12,
            'width': 260,
            'options': [
              {'_c': 'DropdownOption', '_i': 13, 'key': 'food', 'text': 'Food'},
            ],
          },
        },
      },
      backend,
      parent: backend.page,
    );
    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: backend,
        child: MaterialApp(
          home: Scaffold(body: ControlWidget(control: dialog)),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownMenu<String>));
    await tester.pumpAndSettle();
    final menu = tester
        .widget<MenuAnchor>(
          find.descendant(
            of: find.byType(DropdownMenu<String>),
            matching: find.byType(MenuAnchor),
          ),
        )
        .controller!;
    expect(menu.isOpen, isTrue);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(menu.isOpen, isFalse);
    expect(dialog.getBool('open'), isTrue);
    expect(find.text('Flet editor'), findsOneWidget);

    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(dialog.getBool('open'), isFalse);
    expect(find.text('Flet editor'), findsNothing);
  });

  testWidgets('nested Back preserves parent and child drafts and root route', (
    tester,
  ) async {
    final parentDraft = TextEditingController(text: 'Parent draft');
    final childDraft = TextEditingController(text: 'Child draft');
    addTearDown(parentDraft.dispose);
    addTearDown(childDraft.dispose);
    var rootBacks = 0;
    var selections = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: PopScope(
          canPop: false,
          onPopInvokedWithResult: (didPop, result) => rootBacks++,
          child: Builder(
            builder: (context) => TextButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (context) => AlertDialog(
                  content: PopupMenuBackScope(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        TextField(controller: parentDraft),
                        TextButton(
                          onPressed: () => showDialog<void>(
                            context: context,
                            builder: (context) => AlertDialog(
                              content: PopupMenuBackScope(
                                child: Column(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    TextField(controller: childDraft),
                                    DropdownMenu<String>(
                                      onSelected: (_) => selections++,
                                      dropdownMenuEntries: const [
                                        DropdownMenuEntry(
                                          value: 'food',
                                          label: 'Food',
                                        ),
                                      ],
                                    ),
                                  ],
                                ),
                              ),
                              actions: [
                                TextButton(
                                  onPressed: () => Navigator.pop(context),
                                  child: const Text('Child cancel'),
                                ),
                              ],
                            ),
                          ),
                          child: const Text('Open child'),
                        ),
                      ],
                    ),
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Parent cancel'),
                    ),
                  ],
                ),
              ),
              child: const Text('Open parent'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open parent'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open child'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownMenu<String>));
    await tester.pumpAndSettle();
    final menu = tester
        .widget<MenuAnchor>(
          find.descendant(
            of: find.byType(DropdownMenu<String>),
            matching: find.byType(MenuAnchor),
          ),
        )
        .controller!;
    expect(menu.isOpen, isTrue);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(menu.isOpen, isFalse);
    expect(find.text('Child cancel'), findsOneWidget);
    expect(parentDraft.text, 'Parent draft');
    expect(childDraft.text, 'Child draft');
    expect(selections, 0);
    expect(rootBacks, 0);

    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Child cancel'), findsNothing);
    expect(find.text('Parent cancel'), findsOneWidget);
    expect(parentDraft.text, 'Parent draft');
    expect(rootBacks, 0);

    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Parent cancel'), findsNothing);
    expect(rootBacks, 0);
  });

  testWidgets('Back honors another dialog pop guard', (tester) async {
    var blocked = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (context) => AlertDialog(
                content: PopScope(
                  canPop: false,
                  onPopInvokedWithResult: (didPop, result) => blocked++,
                  child: const PopupMenuBackScope(child: Text('Protected')),
                ),
              ),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Protected'), findsOneWidget);
    expect(blocked, greaterThan(0));
  });
}
