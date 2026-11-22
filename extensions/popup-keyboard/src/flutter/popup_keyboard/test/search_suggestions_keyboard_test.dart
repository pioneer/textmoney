import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:popup_keyboard/src/search_suggestions_keyboard.dart';

void main() {
  for (final startFromLast in [false, true]) {
    testWidgets(
      'search arrows focus suggestions and Enter selects ($startFromLast)',
      (tester) async {
        final controller = SearchController();
        final nodes = List.generate(20, (_) => FocusNode());
        var selected = -1;
        addTearDown(controller.dispose);
        for (final node in nodes) {
          addTearDown(node.dispose);
        }
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(
              body: SearchAnchor(
                searchController: controller,
                isFullScreen: false,
                viewConstraints: const BoxConstraints(maxHeight: 240),
                builder: (context, controller) =>
                    SearchBar(controller: controller),
                suggestionsBuilder: (context, controller) => [
                  SearchSuggestionsKeyboard(
                    child: Column(
                      children: [
                        for (var i = 0; i < nodes.length; i++)
                          TextButton(
                            focusNode: nodes[i],
                            onPressed: () {
                              selected = i;
                              controller.closeView(controller.text);
                            },
                            child: Text('Icon $i'),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
        controller.openView();
        await tester.pumpAndSettle();
        final initial = startFromLast ? nodes.length - 1 : 0;
        await tester.sendKeyEvent(
          startFromLast
              ? LogicalKeyboardKey.arrowUp
              : LogicalKeyboardKey.arrowDown,
        );
        await tester.pumpAndSettle();
        expect(nodes[initial].hasPrimaryFocus, isTrue);
        expect(
          tester.getRect(find.text('Icon $initial')).top,
          greaterThanOrEqualTo(0),
        );
        expect(
          tester.getRect(find.text('Icon $initial')).bottom,
          lessThanOrEqualTo(240),
        );
        await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
        await tester.pumpAndSettle();
        expect(nodes[startFromLast ? initial : 1].hasPrimaryFocus, isTrue);
        await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
        await tester.pumpAndSettle();
        final finalIndex = startFromLast ? initial - 1 : 0;
        expect(nodes[finalIndex].hasPrimaryFocus, isTrue);
        await tester.sendKeyEvent(LogicalKeyboardKey.enter);
        await tester.pumpAndSettle();
        expect(selected, finalIndex);
        expect(controller.isOpen, isFalse);
      },
    );
  }

  testWidgets('empty suggestions and a nested dialog do not capture arrows', (
    tester,
  ) async {
    final controller = SearchController();
    final input = FocusNode();
    final suggestion = FocusNode();
    var empty = true;
    late StateSetter updateSuggestions;
    addTearDown(controller.dispose);
    addTearDown(input.dispose);
    addTearDown(suggestion.dispose);
    late BuildContext searchContext;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SearchAnchor(
            searchController: controller,
            builder: (context, controller) => SearchBar(controller: controller),
            suggestionsBuilder: (context, controller) {
              searchContext = context;
              return [
                SearchSuggestionsKeyboard(
                  child: StatefulBuilder(
                    builder: (context, setState) {
                      updateSuggestions = setState;
                      return empty
                          ? const Text('No matching icons')
                          : TextButton(
                              focusNode: suggestion,
                              onPressed: () =>
                                  fail('Nested popup selected an icon'),
                              child: const Text('Icon'),
                            );
                    },
                  ),
                ),
              ];
            },
          ),
        ),
      ),
    );
    controller.openView();
    await tester.pumpAndSettle();
    final searchFocus = FocusManager.instance.primaryFocus;
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
    await tester.pumpAndSettle();
    expect(FocusManager.instance.primaryFocus, searchFocus);
    updateSuggestions(() => empty = false);
    await tester.pumpAndSettle();
    showDialog<void>(
      context: searchContext,
      builder: (_) =>
          AlertDialog(content: TextField(focusNode: input, autofocus: true)),
    );
    await tester.pumpAndSettle();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
    await tester.pumpAndSettle();
    expect(input.hasPrimaryFocus, isTrue);
    expect(suggestion.hasFocus, isFalse);
  });
}
