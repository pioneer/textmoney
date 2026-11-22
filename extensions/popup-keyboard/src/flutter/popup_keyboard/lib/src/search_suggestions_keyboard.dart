import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

class SearchSuggestionsKeyboard extends StatefulWidget {
  const SearchSuggestionsKeyboard({super.key, required this.child});

  final Widget child;

  @override
  State<SearchSuggestionsKeyboard> createState() =>
      _SearchSuggestionsKeyboardState();
}

class _SearchSuggestionsKeyboardState extends State<SearchSuggestionsKeyboard> {
  final _focus = FocusNode(skipTraversal: true);
  ModalRoute<dynamic>? _route;
  FocusScopeNode? _scope;

  @override
  void initState() {
    super.initState();
    FocusManager.instance.addEarlyKeyEventHandler(_onKey);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _route = ModalRoute.of(context);
    _scope = FocusScope.of(context);
  }

  KeyEventResult _onKey(KeyEvent event) {
    if (!mounted ||
        _route?.isCurrent != true ||
        _scope?.hasFocus != true ||
        (event is! KeyDownEvent && event is! KeyRepeatEvent) ||
        HardwareKeyboard.instance.isControlPressed ||
        HardwareKeyboard.instance.isAltPressed ||
        HardwareKeyboard.instance.isMetaPressed ||
        HardwareKeyboard.instance.isShiftPressed) {
      return KeyEventResult.ignored;
    }
    final down = event.logicalKey == LogicalKeyboardKey.arrowDown;
    if (!down && event.logicalKey != LogicalKeyboardKey.arrowUp) {
      return KeyEventResult.ignored;
    }
    final nodes = _focus.traversalDescendants.toList();
    if (nodes.isEmpty) return KeyEventResult.ignored;
    final current = nodes.indexWhere((node) => node.hasFocus);
    final next = current < 0
        ? (down ? 0 : nodes.length - 1)
        : (current + (down ? 1 : -1)).clamp(0, nodes.length - 1);
    final target = nodes[next];
    target.requestFocus();
    final targetContext = target.context;
    if (targetContext != null) {
      Scrollable.ensureVisible(
        targetContext,
        duration: const Duration(milliseconds: 100),
        alignment: 0.5,
      );
    }
    return KeyEventResult.handled;
  }

  @override
  void dispose() {
    FocusManager.instance.removeEarlyKeyEventHandler(_onKey);
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      Focus(focusNode: _focus, includeSemantics: false, child: widget.child);
}
