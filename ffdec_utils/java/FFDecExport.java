import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ScriptPack;
import com.jpexs.decompiler.flash.helpers.HighlightedText;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Decompile selected AS3 classes of a SWF to .as files: FFDecExport <in.swf> <outdir> <class1,class2,...> */
public class FFDecExport {
  public static void main(String[] a) throws Exception {
    List<String> classes = Arrays.asList(a[2].split(","));
    try (InputStream in = new BufferedInputStream(new FileInputStream(a[0]))) {
      SWF swf = new SWF(in, null, false);
      List<ScriptPack> packs = swf.getScriptPacksByClassNames(classes);
      for (ScriptPack pack : packs) {
        HighlightedText text = SWF.getCached(pack);
        String rel = pack.getClassPath().toRawString().replace('.', '/') + ".as";
        Path out = Paths.get(a[1], rel);
        Files.createDirectories(out.getParent());
        Files.write(out, text.text.getBytes(StandardCharsets.UTF_8));
        System.out.println("wrote " + rel);
      }
    }
    System.exit(0);
  }
}
